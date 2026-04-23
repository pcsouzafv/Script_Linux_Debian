from dataclasses import dataclass, field

import httpx

from app.core.config import Settings
from app.schemas.helpdesk import CorrelatedEvent
from app.services.exceptions import IntegrationError


@dataclass(slots=True)
class ZabbixProblemUpdateResult:
    status: str
    mode: str
    event_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class ZabbixClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.zabbix_base_url and self._has_supported_auth())

    async def find_related_events(
        self,
        asset_name: str | None,
        service_name: str | None,
        limit: int = 5,
    ) -> tuple[list[CorrelatedEvent], str, list[str]]:
        if not self.configured:
            return [], "mock", ["Zabbix não configurado; correlação executada em modo mock."]
        if not (service_name or asset_name):
            return [], "live", [
                "Correlação com Zabbix ignorada porque o ticket não possui ativo ou serviço para busca contextual."
            ]

        session_auth: str | None = None
        auth = self.settings.zabbix_api_token
        if not auth:
            session_auth = await self._login()
            auth = session_auth

        try:
            problems = await self._load_problems(
                auth=auth or "",
                asset_name=asset_name,
                service_name=service_name,
                limit=limit,
            )
            host_by_trigger_id = await self._load_problem_hosts(problems, auth or "")
        finally:
            if session_auth:
                await self._logout(session_auth)
        events: list[CorrelatedEvent] = []
        for item in problems:
            trigger_id = str(item.get("objectid") or "")
            events.append(
                CorrelatedEvent(
                    source="zabbix",
                    event_id=str(item.get("eventid", "unknown")),
                    severity=str(item.get("severity", "unknown")),
                    summary=item.get("name", "Evento sem nome"),
                    host=host_by_trigger_id.get(trigger_id),
                )
            )

        return events, "live", ["Correlação consultada no Zabbix com sucesso."]

    async def reconcile_problem_events(
        self,
        *,
        event_ids: list[str] | None = None,
        asset_name: str | None = None,
        service_name: str | None = None,
        message: str,
        close_problem: bool = False,
        limit: int = 10,
    ) -> ZabbixProblemUpdateResult:
        if not self.configured:
            return ZabbixProblemUpdateResult(
                status="mock",
                mode="mock",
                notes=["Zabbix não configurado; reconciliação executada em modo mock."],
            )

        normalized_event_ids = self._normalize_event_ids(event_ids)
        search_term = (service_name or asset_name or "").strip()
        if not normalized_event_ids and not search_term:
            return ZabbixProblemUpdateResult(
                status="noop",
                mode="live",
                notes=["Reconciliação com Zabbix ignorada por falta de event_ids, ativo ou serviço correlacionado."],
            )

        session_auth: str | None = None
        auth = self.settings.zabbix_api_token
        if not auth:
            session_auth = await self._login()
            auth = session_auth

        try:
            problems = await self._load_problems(
                auth=auth or "",
                asset_name=asset_name,
                service_name=service_name,
                event_ids=normalized_event_ids or None,
                limit=max(limit, len(normalized_event_ids) or 0),
                recent=True,
            )
            matched_event_ids = self._extract_problem_event_ids(problems)
            notes: list[str] = []

            if normalized_event_ids and not matched_event_ids and search_term:
                problems = await self._load_problems(
                    auth=auth or "",
                    asset_name=asset_name,
                    service_name=service_name,
                    limit=limit,
                    recent=True,
                )
                matched_event_ids = self._extract_problem_event_ids(problems)
                if matched_event_ids:
                    notes.append(
                        "event_ids correlacionados anteriormente não estavam mais disponíveis; aplicada busca atual por ativo/serviço."
                    )

            if not matched_event_ids:
                notes.append("Nenhum problema ativo ou recente compatível foi encontrado no Zabbix para reconciliação.")
                return ZabbixProblemUpdateResult(
                    status="noop",
                    mode="live",
                    event_ids=[],
                    notes=notes,
                )

            action = 2 | 4
            target_status = "acknowledged"
            if close_problem:
                action |= 1
                target_status = "closed"

            try:
                updated_event_ids = await self._acknowledge_events(
                    auth=auth or "",
                    event_ids=matched_event_ids,
                    message=message,
                    action=action,
                )
                notes.append(
                    f"Reconciliação aplicada no Zabbix para {len(updated_event_ids)} problema(s)."
                )
                return ZabbixProblemUpdateResult(
                    status=target_status,
                    mode="live",
                    event_ids=updated_event_ids,
                    notes=notes,
                )
            except IntegrationError as exc:
                if not close_problem:
                    raise
                fallback_event_ids = await self._acknowledge_events(
                    auth=auth or "",
                    event_ids=matched_event_ids,
                    message=message,
                    action=2 | 4,
                )
                notes.append(
                    "O fechamento manual do problema falhou no Zabbix; mantive acknowledge com comentário para preservar rastreabilidade."
                )
                notes.append(f"Falha ao tentar fechar o problema no Zabbix: {exc}")
                return ZabbixProblemUpdateResult(
                    status="acknowledged",
                    mode="live",
                    event_ids=fallback_event_ids,
                    notes=notes,
                )
        finally:
            if session_auth:
                await self._logout(session_auth)

    def _api_url(self) -> str:
        base_url = self.settings.zabbix_base_url or ""
        if base_url.endswith("api_jsonrpc.php"):
            return base_url
        return f"{base_url.rstrip('/')}/api_jsonrpc.php"

    def _has_supported_auth(self) -> bool:
        return bool(
            self.settings.zabbix_api_token
            or (self.settings.zabbix_username and self.settings.zabbix_password)
        )

    async def _login(self) -> str:
        data = await self._rpc_request(
            "user.login",
            {
                "username": self.settings.zabbix_username,
                "password": self.settings.zabbix_password,
            },
        )

        if "error" in data:
            raise IntegrationError(f"Falha ao autenticar no Zabbix: {data['error']}")

        auth = data.get("result")
        if not isinstance(auth, str) or not auth:
            raise IntegrationError("Zabbix não retornou um token de autenticação válido.")
        return auth

    async def _logout(self, auth: str) -> None:
        try:
            await self._rpc_request("user.logout", [], auth=auth)
        except IntegrationError:
            return

    async def _load_problem_hosts(
        self,
        problems: object,
        auth: str,
    ) -> dict[str, str]:
        if not isinstance(problems, list):
            return {}

        trigger_ids: list[str] = []
        for item in problems:
            if not isinstance(item, dict):
                continue
            trigger_id = str(item.get("objectid") or "").strip()
            if trigger_id:
                trigger_ids.append(trigger_id)

        if not trigger_ids:
            return {}

        trigger_data = await self._rpc_request(
            "trigger.get",
            {
                "output": ["triggerid"],
                "triggerids": trigger_ids,
                "selectHosts": ["host", "name"],
            },
            auth=auth,
        )
        if "error" in trigger_data:
            raise IntegrationError(f"Erro retornado pelo Zabbix: {trigger_data['error']}")

        host_by_trigger_id: dict[str, str] = {}
        for item in trigger_data.get("result", []):
            if not isinstance(item, dict):
                continue
            trigger_id = str(item.get("triggerid") or "").strip()
            hosts = item.get("hosts") or []
            if not trigger_id or not isinstance(hosts, list) or not hosts:
                continue
            first_host = hosts[0]
            if not isinstance(first_host, dict):
                continue
            host_name = first_host.get("name") or first_host.get("host")
            if isinstance(host_name, str) and host_name:
                host_by_trigger_id[trigger_id] = host_name

        return host_by_trigger_id

    async def _load_problems(
        self,
        *,
        auth: str,
        asset_name: str | None = None,
        service_name: str | None = None,
        event_ids: list[str] | None = None,
        limit: int = 5,
        recent: bool = False,
    ) -> list[dict[str, object]]:
        search_term = service_name or asset_name
        if not event_ids and not search_term:
            return []
        params: dict[str, object] = {
            "output": ["eventid", "name", "severity", "objectid"],
            "sortfield": ["eventid"],
            "sortorder": "DESC",
            "limit": max(1, limit),
        }
        if recent:
            params["recent"] = True
        if event_ids:
            params["eventids"] = event_ids
        elif search_term:
            params["search"] = {"name": search_term}

        data = await self._rpc_request("problem.get", params, auth=auth)
        if "error" in data:
            raise IntegrationError(f"Erro retornado pelo Zabbix: {data['error']}")

        result = data.get("result", [])
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    async def _acknowledge_events(
        self,
        *,
        auth: str,
        event_ids: list[str],
        message: str,
        action: int,
    ) -> list[str]:
        data = await self._rpc_request(
            "event.acknowledge",
            {
                "eventids": event_ids,
                "message": message,
                "action": action,
            },
            auth=auth,
        )
        if "error" in data:
            raise IntegrationError(f"Erro retornado pelo Zabbix: {data['error']}")

        result = data.get("result") or {}
        raw_event_ids = result.get("eventids") if isinstance(result, dict) else []
        if not isinstance(raw_event_ids, list):
            return self._normalize_event_ids(event_ids)
        return self._normalize_event_ids(raw_event_ids)

    def _extract_problem_event_ids(self, problems: list[dict[str, object]]) -> list[str]:
        return self._normalize_event_ids(item.get("eventid") for item in problems)

    def _normalize_event_ids(self, event_ids: object) -> list[str]:
        if event_ids is None:
            return []
        normalized: list[str] = []
        for value in event_ids:
            candidate = str(value or "").strip()
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        return normalized

    async def _rpc_request(
        self,
        method: str,
        params: dict[str, object] | list[object],
        *,
        auth: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        headers = {"Content-Type": "application/json-rpc"}
        if auth:
            headers["Authorization"] = f"Bearer {auth}"

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    self._api_url(),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Falha ao comunicar com o Zabbix: {exc}") from exc

        if not isinstance(data, dict):
            raise IntegrationError("Resposta inesperada recebida do Zabbix.")
        return data
