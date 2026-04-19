from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.schemas.helpdesk import TicketOpenRequest, TicketPriority
from app.services.exceptions import IntegrationError, ResourceNotFoundError


@dataclass(slots=True)
class MockTicketRecord:
    ticket_id: str
    subject: str
    description: str
    status: str
    priority: str
    updated_at: str
    requester_glpi_user_id: int | None
    assigned_glpi_user_id: int | None = None
    followups: list[dict[str, object]] = field(default_factory=list)


MOCK_TICKET_STORE: dict[str, MockTicketRecord] = {}


@dataclass(slots=True)
class GLPITicketResult:
    ticket_id: str
    status: str
    mode: str
    notes: list[str]


@dataclass(slots=True)
class GLPITicketDetails:
    ticket_id: str
    subject: str
    status: str
    priority: str | None
    updated_at: str | None
    requester_glpi_user_id: int | None
    assigned_glpi_user_id: int | None
    followup_count: int
    mode: str
    notes: list[str]


@dataclass(slots=True)
class GLPITicketMutationResult:
    ticket: GLPITicketDetails
    mode: str
    notes: list[str]


@dataclass(slots=True)
class GLPIUserRecord:
    user_id: int
    login: str
    firstname: str | None = None
    realname: str | None = None
    phone: str | None = None
    phone2: str | None = None
    mobile: str | None = None
    profile_names: list[str] = field(default_factory=list)
    group_names: list[str] = field(default_factory=list)


class GLPIClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.glpi_base_url and self._has_supported_auth())

    async def create_ticket(self, ticket: TicketOpenRequest) -> GLPITicketResult:
        if not self.configured:
            ticket_id = self._generate_mock_ticket_id()
            MOCK_TICKET_STORE[ticket_id] = MockTicketRecord(
                ticket_id=ticket_id,
                subject=ticket.subject,
                description=ticket.description,
                status="queued-local",
                priority=ticket.priority.value,
                updated_at=self._timestamp(),
                requester_glpi_user_id=ticket.requester.glpi_user_id,
                assigned_glpi_user_id=None,
                followups=[],
            )
            notes = ["GLPI não configurado; ticket criado em modo mock."]
            if ticket.requester.glpi_user_id:
                notes.append(
                    f"Solicitante vinculado localmente ao usuário GLPI {ticket.requester.glpi_user_id}."
                )
            return GLPITicketResult(
                ticket_id=ticket_id,
                status="queued-local",
                mode="mock",
                notes=notes,
            )

        session_token = await self._open_session()
        try:
            headers = self._session_headers(session_token, with_json_content_type=True)
            payload = {
                "input": {
                    "name": ticket.subject,
                    "content": ticket.description,
                    "priority": self._map_priority(ticket.priority),
                }
            }
            if ticket.requester.glpi_user_id:
                payload["input"]["_users_id_requester"] = ticket.requester.glpi_user_id

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.post(
                        f"{self._base_url()}/Ticket/",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao criar ticket no GLPI: {exc}") from exc

            ticket_id = str(data.get("id") or data.get("ID") or "")
            if not ticket_id:
                raise IntegrationError("GLPI não retornou um identificador de ticket.")

            notes = ["Ticket criado com sucesso no GLPI."]
            if ticket.requester.glpi_user_id:
                notes.append(
                    f"Solicitante vinculado ao usuário GLPI {ticket.requester.glpi_user_id}."
                )

            return GLPITicketResult(
                ticket_id=ticket_id,
                status="created",
                mode="live",
                notes=notes,
            )
        finally:
            await self._close_session(session_token)

    async def get_ticket(self, ticket_id: str) -> GLPITicketDetails:
        if not self.configured:
            mock_ticket = MOCK_TICKET_STORE.get(ticket_id)
            if not mock_ticket:
                raise ResourceNotFoundError(
                    f"Ticket {ticket_id} não encontrado no modo mock."
                )

            return GLPITicketDetails(
                ticket_id=mock_ticket.ticket_id,
                subject=mock_ticket.subject,
                status=mock_ticket.status,
                priority=mock_ticket.priority,
                updated_at=mock_ticket.updated_at,
                requester_glpi_user_id=mock_ticket.requester_glpi_user_id,
                assigned_glpi_user_id=mock_ticket.assigned_glpi_user_id,
                followup_count=len(mock_ticket.followups),
                mode="mock",
                notes=["Ticket consultado no armazenamento mock local."],
            )

        session_token = await self._open_session()
        try:
            headers = self._session_headers(session_token, with_json_content_type=True)

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        f"{self._base_url()}/Ticket/{ticket_id}",
                        headers=headers,
                    )
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao consultar ticket no GLPI: {exc}") from exc

            if response.status_code == 404:
                raise ResourceNotFoundError(f"Ticket {ticket_id} não encontrado no GLPI.")

            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao consultar ticket no GLPI: {exc}") from exc

            data = response.json()
            requester_glpi_user_id = self._extract_actor_id(data.get("_users_id_requester"))
            assigned_glpi_user_id = self._extract_actor_id(data.get("_users_id_assign"))
            if requester_glpi_user_id is None or assigned_glpi_user_id is None:
                linked_requester_id, linked_assignee_id = await self._load_ticket_user_actor_ids(
                    ticket_id=str(data.get("id") or ticket_id),
                    session_token=session_token,
                )
                if requester_glpi_user_id is None:
                    requester_glpi_user_id = linked_requester_id
                if assigned_glpi_user_id is None:
                    assigned_glpi_user_id = linked_assignee_id

            return GLPITicketDetails(
                ticket_id=str(data.get("id") or ticket_id),
                subject=data.get("name") or f"Ticket {ticket_id}",
                status=self._normalize_status(data.get("status")),
                priority=self._normalize_priority(data.get("priority")),
                updated_at=data.get("date_mod") or data.get("date") or data.get("solvedate"),
                requester_glpi_user_id=requester_glpi_user_id,
                assigned_glpi_user_id=assigned_glpi_user_id,
                followup_count=self._extract_followup_count(data),
                mode="live",
                notes=["Ticket consultado com sucesso no GLPI."],
            )
        finally:
            await self._close_session(session_token)

    async def find_user_by_id(self, user_id: int) -> GLPIUserRecord:
        if not self.configured:
            raise IntegrationError(
                "A resolução de identidade via GLPI exige GLPI configurado no backend."
            )

        session_token = await self._open_session()
        try:
            headers = self._session_headers(session_token, with_json_content_type=True)

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        f"{self._base_url()}/User/{user_id}",
                        headers=headers,
                    )
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao consultar usuário no GLPI: {exc}") from exc

            if response.status_code == 404:
                raise ResourceNotFoundError(f"Usuário GLPI {user_id} não encontrado.")

            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao consultar usuário no GLPI: {exc}") from exc

            data = response.json()
            return GLPIUserRecord(
                user_id=int(data.get("id") or user_id),
                login=str(data.get("name") or ""),
                firstname=self._normalize_optional_text(data.get("firstname")),
                realname=self._normalize_optional_text(data.get("realname")),
                phone=self._normalize_optional_text(data.get("phone")),
                phone2=self._normalize_optional_text(data.get("phone2")),
                mobile=self._normalize_optional_text(data.get("mobile")),
                profile_names=[],
                group_names=[],
            )
        finally:
            await self._close_session(session_token)

    async def list_tickets_for_requester(
        self,
        requester_glpi_user_id: int,
        *,
        include_closed: bool = False,
        limit: int = 5,
        allowed_statuses: set[str] | None = None,
    ) -> list[GLPITicketDetails]:
        if requester_glpi_user_id <= 0 or limit <= 0:
            return []

        normalized_allowed_statuses = {
            status.strip().lower() for status in (allowed_statuses or set()) if status.strip()
        }

        if not self.configured:
            tickets = [
                GLPITicketDetails(
                    ticket_id=record.ticket_id,
                    subject=record.subject,
                    status=record.status,
                    priority=record.priority,
                    updated_at=record.updated_at,
                    requester_glpi_user_id=record.requester_glpi_user_id,
                    assigned_glpi_user_id=record.assigned_glpi_user_id,
                    followup_count=len(record.followups),
                    mode="mock",
                    notes=["Tickets do solicitante consultados no armazenamento mock local."],
                )
                for record in MOCK_TICKET_STORE.values()
                if record.requester_glpi_user_id == requester_glpi_user_id
            ]
            tickets.sort(key=lambda item: item.updated_at or "", reverse=True)
            if not include_closed:
                tickets = [ticket for ticket in tickets if ticket.status != "closed"]
            if normalized_allowed_statuses:
                tickets = [
                    ticket
                    for ticket in tickets
                    if ticket.status.strip().lower() in normalized_allowed_statuses
                ]
            return tickets[:limit]

        session_token = await self._open_session()
        try:
            headers = self._session_headers(session_token, with_json_content_type=True)
            params: list[tuple[str, str]] = [
                ("criteria[0][field]", "4"),
                ("criteria[0][searchtype]", "equals"),
                ("criteria[0][value]", str(requester_glpi_user_id)),
                ("forcedisplay[0]", "2"),
                ("forcedisplay[1]", "1"),
                ("forcedisplay[2]", "12"),
                ("forcedisplay[3]", "19"),
                ("sort", "19"),
                ("order", "DESC"),
                ("range", f"0-{limit - 1}"),
            ]

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        f"{self._base_url()}/search/Ticket",
                        headers=headers,
                        params=params,
                    )
                    response.raise_for_status()
                    data = response.json()
            except httpx.HTTPError as exc:
                raise IntegrationError(
                    f"Falha ao listar tickets do solicitante no GLPI: {exc}"
                ) from exc

            rows = data.get("data") or []
            if isinstance(rows, dict):
                rows = list(rows.values())

            tickets: list[GLPITicketDetails] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ticket_id = row.get("2") or row.get("id")
                if ticket_id in (None, ""):
                    continue
                status = self._normalize_status(row.get("12"))
                if not include_closed and status == "closed":
                    continue
                if normalized_allowed_statuses and status.strip().lower() not in normalized_allowed_statuses:
                    continue
                tickets.append(
                    GLPITicketDetails(
                        ticket_id=str(ticket_id),
                        subject=str(row.get("1") or f"Ticket {ticket_id}"),
                        status=status,
                        priority=None,
                        updated_at=self._normalize_optional_text(row.get("19")),
                        requester_glpi_user_id=requester_glpi_user_id,
                        assigned_glpi_user_id=None,
                        followup_count=0,
                        mode="live",
                        notes=["Tickets do solicitante consultados com sucesso no GLPI."],
                    )
                )
            return tickets
        finally:
            await self._close_session(session_token)

    async def add_ticket_followup(
        self,
        ticket_id: str,
        content: str,
        author_glpi_user_id: int | None = None,
    ) -> GLPITicketMutationResult:
        if not self.configured:
            mock_ticket = self._get_mock_ticket(ticket_id)
            mock_ticket.followups.append(
                {
                    "content": content,
                    "author_glpi_user_id": author_glpi_user_id,
                    "created_at": self._timestamp(),
                }
            )
            mock_ticket.updated_at = self._timestamp()
            ticket = await self.get_ticket(ticket_id)
            notes = ["Comentário adicionado localmente ao ticket em modo mock."]
            if author_glpi_user_id:
                notes.append(f"Comentário associado ao usuário GLPI {author_glpi_user_id}.")
            return GLPITicketMutationResult(ticket=ticket, mode="mock", notes=notes)

        session_token = await self._open_session()
        try:
            headers = self._session_headers(session_token, with_json_content_type=True)
            payload = {
                "input": {
                    "itemtype": "Ticket",
                    "items_id": self._coerce_live_id(ticket_id),
                    "content": content,
                }
            }
            if author_glpi_user_id:
                payload["input"]["users_id"] = author_glpi_user_id

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.post(
                        f"{self._base_url()}/ITILFollowup/",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao adicionar comentário no GLPI: {exc}") from exc
        finally:
            await self._close_session(session_token)

        ticket = await self.get_ticket(ticket_id)
        notes = ["Comentário adicionado com sucesso ao ticket no GLPI."]
        if author_glpi_user_id:
            notes.append(f"Comentário associado ao usuário GLPI {author_glpi_user_id}.")
        return GLPITicketMutationResult(ticket=ticket, mode="live", notes=notes)

    async def update_ticket_status(
        self,
        ticket_id: str,
        status_name: str,
    ) -> GLPITicketMutationResult:
        mapped_status = self._status_name_to_glpi_value(status_name)
        if not self.configured:
            mock_ticket = self._get_mock_ticket(ticket_id)
            mock_ticket.status = status_name
            mock_ticket.updated_at = self._timestamp()
            ticket = await self.get_ticket(ticket_id)
            return GLPITicketMutationResult(
                ticket=ticket,
                mode="mock",
                notes=[f"Status do ticket atualizado localmente para {status_name}."],
            )

        result = await self._update_ticket_fields(
            ticket_id=ticket_id,
            fields={"status": mapped_status},
            success_note=f"Status do ticket atualizado para {status_name} no GLPI.",
        )
        return result

    async def assign_ticket(
        self,
        ticket_id: str,
        assignee_glpi_user_id: int,
    ) -> GLPITicketMutationResult:
        if not self.configured:
            mock_ticket = self._get_mock_ticket(ticket_id)
            mock_ticket.assigned_glpi_user_id = assignee_glpi_user_id
            mock_ticket.updated_at = self._timestamp()
            ticket = await self.get_ticket(ticket_id)
            return GLPITicketMutationResult(
                ticket=ticket,
                mode="mock",
                notes=[f"Ticket atribuído localmente ao usuário GLPI {assignee_glpi_user_id}."],
            )

        result = await self._update_ticket_fields(
            ticket_id=ticket_id,
            fields={"_users_id_assign": assignee_glpi_user_id},
            success_note=f"Ticket atribuído ao usuário GLPI {assignee_glpi_user_id}.",
        )
        return result

    async def find_user_by_phone(self, phone_number: str) -> GLPIUserRecord:
        if not self.configured:
            raise IntegrationError(
                "A resolução de identidade via GLPI exige GLPI configurado no backend."
            )

        candidates = await self._search_users(
            criteria=[
                {"field": 6, "searchtype": "contains", "value": phone_number},
                {"link": "OR", "field": 10, "searchtype": "contains", "value": phone_number},
                {"link": "OR", "field": 11, "searchtype": "contains", "value": phone_number},
                {"link": "AND", "field": 8, "searchtype": "equals", "value": 1},
            ],
            range_="0-9",
        )
        return self._resolve_single_user_match(
            candidates,
            match_label=f"número {phone_number}",
            exact_predicate=lambda user: self._matches_phone(user, phone_number),
        )

    async def find_user_by_identifier(self, identifier: str) -> GLPIUserRecord:
        normalized_phone = self._normalize_phone(identifier)
        if normalized_phone:
            try:
                return await self.find_user_by_phone(identifier)
            except ResourceNotFoundError:
                pass

        candidates = await self._search_users(
            criteria=[
                {"field": 1, "searchtype": "contains", "value": identifier},
                {"link": "OR", "field": 9, "searchtype": "contains", "value": identifier},
                {"link": "OR", "field": 34, "searchtype": "contains", "value": identifier},
                {"link": "AND", "field": 8, "searchtype": "equals", "value": 1},
            ],
            range_="0-19",
        )
        lowered_identifier = identifier.strip().lower()
        return self._resolve_single_user_match(
            candidates,
            match_label=f"identificador {identifier}",
            exact_predicate=lambda user: (
                user.login.lower() == lowered_identifier
                or self._display_name(user).lower() == lowered_identifier
            ),
        )

    async def _open_session(self) -> str:
        headers = self._init_session_headers()

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(f"{self._base_url()}/initSession", headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Falha ao iniciar sessão com a API do GLPI: {exc}") from exc

        session_token = data.get("session_token")
        if not session_token:
            raise IntegrationError("Falha ao iniciar sessão com a API do GLPI.")
        return session_token

    async def _close_session(self, session_token: str) -> None:
        headers = self._session_headers(session_token, with_json_content_type=True)
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.get(f"{self._base_url()}/killSession", headers=headers)
            except httpx.HTTPError:
                return

    def _base_url(self) -> str:
        return (self.settings.glpi_base_url or "").rstrip("/")

    def _map_priority(self, priority: TicketPriority) -> int:
        priority_map = {
            TicketPriority.LOW: 2,
            TicketPriority.MEDIUM: 3,
            TicketPriority.HIGH: 4,
            TicketPriority.CRITICAL: 5,
        }
        return priority_map[priority]

    def _normalize_status(self, status: object) -> str:
        status_map = {
            1: "new",
            2: "processing",
            3: "planned",
            4: "waiting",
            5: "solved",
            6: "closed",
        }
        if isinstance(status, int):
            return status_map.get(status, f"status-{status}")
        return str(status or "unknown")

    def _normalize_priority(self, priority: object) -> str | None:
        priority_map = {
            1: "very-low",
            2: "low",
            3: "medium",
            4: "high",
            5: "very-high",
            6: "major",
        }
        if priority is None:
            return None
        if isinstance(priority, int):
            return priority_map.get(priority, f"priority-{priority}")
        return str(priority)

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _generate_mock_ticket_id(self) -> str:
        return f"GLPI-LOCAL-{uuid4().hex[:12].upper()}"

    async def _update_ticket_fields(
        self,
        ticket_id: str,
        fields: dict[str, object],
        success_note: str,
    ) -> GLPITicketMutationResult:
        session_token = await self._open_session()
        try:
            headers = self._session_headers(session_token, with_json_content_type=True)
            payload = {
                "input": {
                    "id": self._coerce_live_id(ticket_id),
                    **fields,
                }
            }

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.put(
                        f"{self._base_url()}/Ticket/{ticket_id}",
                        headers=headers,
                        json=payload,
                    )
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao atualizar ticket no GLPI: {exc}") from exc

            if response.status_code == 404:
                raise ResourceNotFoundError(f"Ticket {ticket_id} não encontrado no GLPI.")

            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao atualizar ticket no GLPI: {exc}") from exc
        finally:
            await self._close_session(session_token)

        ticket = await self.get_ticket(ticket_id)
        return GLPITicketMutationResult(ticket=ticket, mode="live", notes=[success_note])

    async def _load_ticket_user_actor_ids(
        self,
        ticket_id: str,
        session_token: str,
    ) -> tuple[int | None, int | None]:
        headers = self._session_headers(session_token, with_json_content_type=True)

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    f"{self._base_url()}/Ticket/{ticket_id}/Ticket_User/",
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Falha ao consultar participantes do ticket no GLPI: {exc}") from exc

        return self._extract_ticket_user_actor_ids(data)

    async def _search_users(
        self,
        criteria: list[dict[str, object]],
        *,
        range_: str,
    ) -> list[GLPIUserRecord]:
        session_token = await self._open_session()
        try:
            headers = self._session_headers(session_token, with_json_content_type=True)
            params: list[tuple[str, str]] = []
            for index, criterion in enumerate(criteria):
                for key, value in criterion.items():
                    params.append((f"criteria[{index}][{key}]", str(value)))

            for display_index, field_id in enumerate((2, 1, 9, 34, 6, 10, 11, 13, 20, 8)):
                params.append((f"forcedisplay[{display_index}]", str(field_id)))
            params.append(("range", range_))

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        f"{self._base_url()}/search/User",
                        headers=headers,
                        params=params,
                    )
                    response.raise_for_status()
                    data = response.json()
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Falha ao consultar usuários no GLPI: {exc}") from exc

            rows = data.get("data") or []
            if isinstance(rows, dict):
                rows = list(rows.values())

            users: list[GLPIUserRecord] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                user_id = row.get("2") or row.get("id")
                if user_id in (None, ""):
                    continue
                users.append(
                    GLPIUserRecord(
                        user_id=int(user_id),
                        login=str(row.get("1") or ""),
                        firstname=self._normalize_optional_text(row.get("9")),
                        realname=self._normalize_optional_text(row.get("34")),
                        phone=self._normalize_optional_text(row.get("6")),
                        phone2=self._normalize_optional_text(row.get("10")),
                        mobile=self._normalize_optional_text(row.get("11")),
                        profile_names=self._normalize_multivalue_field(row.get("20")),
                        group_names=self._normalize_multivalue_field(row.get("13")),
                    )
                )
            return users
        finally:
            await self._close_session(session_token)

    def _resolve_single_user_match(
        self,
        candidates: list[GLPIUserRecord],
        *,
        match_label: str,
        exact_predicate,
    ) -> GLPIUserRecord:
        if not candidates:
            raise ResourceNotFoundError(
                f"Nenhum usuário GLPI ativo encontrado para {match_label}."
            )

        exact_matches = [candidate for candidate in candidates if exact_predicate(candidate)]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(candidates) == 1:
            return candidates[0]

        raise IntegrationError(
            f"Mais de um usuário GLPI corresponde a {match_label}; revise os telefones ou identificadores cadastrados."
        )

    def _has_supported_auth(self) -> bool:
        return self._uses_user_token_auth() or self._uses_basic_auth()

    def _uses_user_token_auth(self) -> bool:
        return bool(self.settings.glpi_user_token)

    def _uses_basic_auth(self) -> bool:
        return bool(self.settings.glpi_username and self.settings.glpi_password)

    def _init_session_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.glpi_app_token:
            headers["App-Token"] = self.settings.glpi_app_token

        if self._uses_user_token_auth():
            headers["Authorization"] = f"user_token {self.settings.glpi_user_token}"
            return headers

        if self._uses_basic_auth():
            credentials = (
                f"{self.settings.glpi_username}:{self.settings.glpi_password}".encode("utf-8")
            )
            headers["Authorization"] = f"Basic {b64encode(credentials).decode('ascii')}"
            return headers

        raise IntegrationError(
            "GLPI configurado sem método de autenticação compatível. "
            "Use user_token ou username/password."
        )

    def _session_headers(
        self,
        session_token: str,
        *,
        with_json_content_type: bool = False,
    ) -> dict[str, str]:
        headers = {"Session-Token": session_token}
        if self.settings.glpi_app_token:
            headers["App-Token"] = self.settings.glpi_app_token
        if with_json_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def _get_mock_ticket(self, ticket_id: str) -> MockTicketRecord:
        mock_ticket = MOCK_TICKET_STORE.get(ticket_id)
        if not mock_ticket:
            raise ResourceNotFoundError(f"Ticket {ticket_id} não encontrado no modo mock.")
        return mock_ticket

    def _status_name_to_glpi_value(self, status_name: str) -> int:
        status_map = {
            "new": 1,
            "processing": 2,
            "planned": 3,
            "waiting": 4,
            "solved": 5,
            "closed": 6,
        }
        normalized_name = status_name.strip().lower()
        if normalized_name not in status_map:
            raise IntegrationError(f"Status inválido para GLPI: {status_name}")
        return status_map[normalized_name]

    def _coerce_live_id(self, ticket_id: str) -> int | str:
        return int(ticket_id) if str(ticket_id).isdigit() else ticket_id

    def _extract_actor_id(self, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, list) and value:
            first_value = value[0]
            if isinstance(first_value, dict):
                actor_id = first_value.get("users_id") or first_value.get("id")
                if isinstance(actor_id, int):
                    return actor_id
            if isinstance(first_value, int):
                return first_value
        if isinstance(value, dict):
            actor_id = value.get("users_id") or value.get("id")
            if isinstance(actor_id, int):
                return actor_id
        return None

    def _extract_followup_count(self, data: dict[str, object]) -> int:
        for field_name in ("_itilfollowups", "itilfollowups", "followups"):
            field_value = data.get(field_name)
            if isinstance(field_value, list):
                return len(field_value)
        return 0

    def _extract_ticket_user_actor_ids(self, data: object) -> tuple[int | None, int | None]:
        requester_glpi_user_id: int | None = None
        assigned_glpi_user_id: int | None = None

        if not isinstance(data, list):
            return requester_glpi_user_id, assigned_glpi_user_id

        for item in data:
            if not isinstance(item, dict):
                continue
            users_id = item.get("users_id")
            user_type = item.get("type")
            if not isinstance(users_id, int) or not isinstance(user_type, int):
                continue
            if user_type == 1 and requester_glpi_user_id is None:
                requester_glpi_user_id = users_id
            if user_type == 2 and assigned_glpi_user_id is None:
                assigned_glpi_user_id = users_id

        return requester_glpi_user_id, assigned_glpi_user_id

    def _matches_phone(self, user: GLPIUserRecord, phone_number: str) -> bool:
        normalized_phone = self._normalize_phone(phone_number)
        if not normalized_phone:
            return False
        return normalized_phone in {
            self._normalize_phone(user.phone),
            self._normalize_phone(user.phone2),
            self._normalize_phone(user.mobile),
        }

    def _display_name(self, user: GLPIUserRecord) -> str:
        parts = [part for part in (user.firstname, user.realname) if part]
        if parts:
            return " ".join(parts)
        return user.login

    def _normalize_optional_text(self, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or normalized == "&nbsp;":
            return None
        return normalized

    def _normalize_multivalue_field(self, value: object) -> list[str]:
        normalized = self._normalize_optional_text(value)
        if not normalized:
            return []
        return [normalized]

    def _normalize_phone(self, phone_number: object) -> str:
        return "".join(character for character in str(phone_number or "") if character.isdigit())
