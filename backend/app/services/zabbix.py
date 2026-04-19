import httpx

from app.core.config import Settings
from app.schemas.helpdesk import CorrelatedEvent
from app.services.exceptions import IntegrationError


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

        search_term = service_name or asset_name
        params: dict[str, object] = {
            "output": ["eventid", "name", "severity", "objectid"],
            "sortfield": ["eventid"],
            "sortorder": "DESC",
            "limit": limit,
        }
        if search_term:
            params["search"] = {"name": search_term}

        session_auth: str | None = None
        auth = self.settings.zabbix_api_token
        if not auth:
            session_auth = await self._login()
            auth = session_auth

        try:
            data = await self._rpc_request("problem.get", params, auth=auth)
            if "error" in data:
                raise IntegrationError(f"Erro retornado pelo Zabbix: {data['error']}")

            host_by_trigger_id = await self._load_problem_hosts(data.get("result", []), auth or "")
        finally:
            if session_auth:
                await self._logout(session_auth)
        events: list[CorrelatedEvent] = []
        for item in data.get("result", []):
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
