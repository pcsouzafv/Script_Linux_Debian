import asyncio

from app.core.config import Settings
from app.schemas.helpdesk import TicketOpenRequest, TicketPriority
from app.services.glpi import GLPIClient


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_search_users_requests_id_field_and_parses_phone_result(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["params"] = params or []
            return _FakeResponse(
                {
                    "data": [
                        {
                            "2": "2",
                            "1": "glpi",
                            "6": "+5521972008679",
                            "10": None,
                            "11": "+5521972008679",
                            "9": "Paula",
                            "34": "Almeida",
                            "13": "service-desk",
                            "20": "Super-Admin",
                            "8": 1,
                        }
                    ]
                }
            )

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    users = asyncio.run(
        client._search_users(
            criteria=[
                {"field": 11, "searchtype": "contains", "value": "5521972008679"},
                {"link": "AND", "field": 8, "searchtype": "equals", "value": 1},
            ],
            range_="0-9",
        )
    )

    forcedisplay_values = [
        value
        for key, value in captured["params"]
        if str(key).startswith("forcedisplay[")
    ]

    assert "2" in forcedisplay_values
    assert len(users) == 1
    assert users[0].user_id == 2
    assert users[0].login == "glpi"
    assert users[0].mobile == "+5521972008679"
    assert users[0].profile_names == ["Super-Admin"]


def test_list_tickets_for_requester_uses_search_ticket_and_filters_closed(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["params"] = params or []
            return _FakeResponse(
                {
                    "data": [
                        {
                            "2": 23,
                            "1": "Chamado mais recente",
                            "12": 5,
                            "19": "2026-04-19 20:17:58",
                            "4": "7",
                        },
                        {
                            "2": 22,
                            "1": "Chamado em andamento",
                            "12": 2,
                            "19": "2026-04-19 20:16:30",
                            "4": "7",
                        },
                        {
                            "2": 21,
                            "1": "Chamado novo que não deve entrar",
                            "12": 1,
                            "19": "2026-04-19 20:16:00",
                            "4": "7",
                        },
                    ]
                }
            )

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    tickets = asyncio.run(
        client.list_tickets_for_requester(
            7,
            limit=3,
            allowed_statuses={"processing", "solved"},
        )
    )

    params = dict(captured["params"])
    assert captured["url"].endswith("/search/Ticket")
    assert params["criteria[0][field]"] == "4"
    assert params["criteria[0][value]"] == "7"
    assert params["sort"] == "19"
    assert params["order"] == "DESC"
    assert params["range"] == "0-2"
    assert [ticket.ticket_id for ticket in tickets] == ["23", "22"]
    assert tickets[0].status == "solved"
    assert tickets[1].status == "processing"


def test_get_ticket_loads_requester_and_assignee_from_ticket_user_when_missing(monkeypatch) -> None:
    responses = {
        "http://127.0.0.1:8088/apirest.php/Ticket/20": {
            "id": 20,
            "name": "Chamado com relacionamento externo",
            "status": 1,
            "priority": 3,
            "date_mod": "2026-04-19 21:20:19",
        },
        "http://127.0.0.1:8088/apirest.php/Ticket/20/Ticket_User/": [
            {"users_id": 7, "type": 1},
            {"users_id": 4, "type": 2},
        ],
    }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            return _FakeResponse(responses[url])

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    ticket = asyncio.run(client.get_ticket("20"))

    assert ticket.ticket_id == "20"
    assert ticket.requester_glpi_user_id == 7
    assert ticket.assigned_glpi_user_id == 4


def test_create_ticket_enriches_request_type_category_and_linked_item(monkeypatch) -> None:
    captured: dict[str, object] = {
        "posts": [],
        "gets": [],
    }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            captured["gets"].append({"url": url, "params": params or []})
            if url.endswith("/search/ITILCategory"):
                return _FakeResponse({"data": [{"2": 11, "1": "Acesso", "3": "Acesso"}]})
            if url.endswith("/search/Computer"):
                return _FakeResponse({"data": [{"2": 9, "1": "erp-web-01"}]})
            if url.endswith("/search/NetworkEquipment") or url.endswith("/search/Printer"):
                return _FakeResponse({"data": []})
            raise AssertionError(f"GET inesperado: {url}")

        async def post(self, url: str, headers: dict | None = None, json: dict | None = None):
            captured["posts"].append({"url": url, "json": json or {}})
            if url.endswith("/Ticket/"):
                return _FakeResponse({"id": 42})
            if url.endswith("/Item_Ticket/"):
                return _FakeResponse({"id": 99})
            raise AssertionError(f"POST inesperado: {url}")

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    result = asyncio.run(
        client.create_ticket(
            TicketOpenRequest(
                subject="WhatsApp: Estou sem acesso ao ERP",
                description="Usuário relata indisponibilidade do ERP desde 08:10.",
                category="acesso",
                asset_name="erp-web-01",
                service_name="erp",
                priority=TicketPriority.HIGH,
                requester={
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "glpi_user_id": 7,
                },
            )
        )
    )

    ticket_payload = captured["posts"][0]["json"]
    link_payload = captured["posts"][1]["json"]

    assert ticket_payload["input"]["requesttypes_id"] == 3
    assert ticket_payload["input"]["itilcategories_id"] == 11
    assert ticket_payload["input"]["_users_id_requester"] == 7
    assert ticket_payload["input"]["externalid"].startswith("helpdesk-whatsapp-")
    assert link_payload["input"] == {
        "itemtype": "Computer",
        "items_id": 9,
        "tickets_id": 42,
    }
    assert result.ticket_id == "42"
    assert result.request_type_id == 3
    assert result.request_type_name == "Phone"
    assert result.category_id == 11
    assert result.category_name == "Acesso"
    assert result.linked_item_type == "Computer"
    assert result.linked_item_id == 9
    assert result.linked_item_name == "erp-web-01"
    assert any("Item_Ticket" in note for note in result.notes)


def test_list_ticket_ids_uses_search_ticket(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            captured["url"] = url
            captured["params"] = params or []
            return _FakeResponse(
                {
                    "data": [
                        {"2": 23, "1": "Mais recente"},
                        {"2": 22, "1": "Anterior"},
                    ]
                }
            )

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    ticket_ids = asyncio.run(client.list_ticket_ids(limit=2, offset=3))

    params = dict(captured["params"])
    assert captured["url"].endswith("/search/Ticket")
    assert params["range"] == "3-4"
    assert ticket_ids == ["23", "22"]


def test_get_ticket_analytics_details_parses_external_id_request_type_and_category(monkeypatch) -> None:
    responses = {
        "http://127.0.0.1:8088/apirest.php/Ticket/20": {
            "id": 20,
            "name": "Chamado histórico",
            "content": "Origem: WhatsApp\\nResumo informado: Não consigo acessar o ERP.",
            "status": 1,
            "priority": 4,
            "date_mod": "2026-04-20 10:00:00",
            "externalid": "helpdesk-whatsapp-historical-20",
            "requesttypes_id": 3,
            "itilcategories_id": 11,
            "_users_id_requester": [{"users_id": 7}],
            "_users_id_assign": [{"users_id": 4}],
            "_itilcategories_id": "Acesso",
        },
    }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            return _FakeResponse(responses[url])

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    details = asyncio.run(client.get_ticket_analytics_details("20"))

    assert details.ticket_id == "20"
    assert details.external_id == "helpdesk-whatsapp-historical-20"
    assert details.request_type_id == 3
    assert details.request_type_name == "Phone"
    assert details.category_id == 11
    assert details.category_name == "Acesso"
    assert details.requester_glpi_user_id == 7
    assert details.assigned_glpi_user_id == 4


def test_get_ticket_analytics_details_resolves_category_name_by_id_when_label_missing(monkeypatch) -> None:
    responses = {
        "http://127.0.0.1:8088/apirest.php/Ticket/20": {
            "id": 20,
            "name": "Chamado histórico",
            "content": "Origem: WhatsApp\nResumo informado: Não consigo acessar o ERP.",
            "status": 1,
            "priority": 4,
            "date_mod": "2026-04-20 10:00:00",
            "externalid": "helpdesk-whatsapp-historical-20",
            "requesttypes_id": 3,
            "itilcategories_id": 11,
            "_users_id_requester": [{"users_id": 7}],
            "_users_id_assign": [{"users_id": 4}],
            "_itilcategories_id": None,
        },
        "http://127.0.0.1:8088/apirest.php/ITILCategory/11": {
            "id": 11,
            "name": "Acesso",
        },
    }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            return _FakeResponse(responses[url])

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    details = asyncio.run(client.get_ticket_analytics_details("20"))

    assert details.category_id == 11
    assert details.category_name == "Acesso"


def test_get_ticket_resolution_context_parses_followups_and_solutions(monkeypatch) -> None:
    responses = {
        "http://127.0.0.1:8088/apirest.php/Ticket/20/ITILSolution/": [
            {
                "content": "Senha sincronizada no AD e validada com o usuario.",
                "date_creation": "2026-04-20 09:15:00",
                "users_id": 12,
            }
        ],
        "http://127.0.0.1:8088/apirest.php/Ticket/20/ITILFollowup/": [
            {
                "content": "Usuario confirmou que o erro ocorre apenas no ERP web.",
                "date_creation": "2026-04-20 08:55:00",
                "users_id": 9,
            }
        ],
    }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            return _FakeResponse(responses[url])

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    context = asyncio.run(client.get_ticket_resolution_context("20", limit=5))

    assert context.mode == "live"
    assert [entry.source for entry in context.entries] == ["solution", "followup"]
    assert context.entries[0].author_glpi_user_id == 12
    assert "Senha sincronizada" in context.entries[0].content


def test_add_ticket_solution_posts_itilsolution_payload(monkeypatch) -> None:
    captured: dict[str, object] = {"posts": []}

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict | None = None, json: dict | None = None):
            captured["posts"].append({"url": url, "json": json or {}})
            return _FakeResponse({"id": 99})

        async def get(self, url: str, headers: dict | None = None, params: list[tuple[str, str]] | None = None):
            return _FakeResponse(
                {
                    "id": 42,
                    "name": "Chamado resolvido",
                    "status": 5,
                    "priority": 3,
                    "date_mod": "2026-04-21 11:00:00",
                    "_users_id_requester": [{"users_id": 7}],
                    "_users_id_assign": [{"users_id": 9}],
                }
            )

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    result = asyncio.run(
        client.add_ticket_solution(
            "42",
            "Resumo da resolucao: validacao concluida.",
            author_glpi_user_id=9,
        )
    )

    assert captured["posts"][0]["url"].endswith("/ITILSolution/")
    assert captured["posts"][0]["json"] == {
        "input": {
            "itemtype": "Ticket",
            "items_id": 42,
            "content": "Resumo da resolucao: validacao concluida.",
            "users_id": 9,
        }
    }
    assert result.ticket.status == "solved"


def test_apply_ticket_analytics_patch_updates_ticket_and_links_item(monkeypatch) -> None:
    captured: dict[str, object] = {
        "puts": [],
        "posts": [],
    }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def put(self, url: str, headers: dict | None = None, json: dict | None = None):
            captured["puts"].append({"url": url, "json": json or {}})
            return _FakeResponse({"id": 42})

        async def post(self, url: str, headers: dict | None = None, json: dict | None = None):
            captured["posts"].append({"url": url, "json": json or {}})
            return _FakeResponse({"id": 99})

    monkeypatch.setattr("app.services.glpi.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(GLPIClient, "_open_session", lambda self: asyncio.sleep(0, result="session-token"))
    monkeypatch.setattr(GLPIClient, "_close_session", lambda self, session_token: asyncio.sleep(0))

    client = GLPIClient(
        Settings(
            _env_file=None,
            glpi_base_url="http://127.0.0.1:8088/apirest.php",
            glpi_username="glpi",
            glpi_password="glpi",
        )
    )

    result = asyncio.run(
        client.apply_ticket_analytics_patch(
            "42",
            external_id="helpdesk-whatsapp-historical-42",
            request_type_id=3,
            category_id=11,
            category_name="Acesso",
            linked_item=client._parse_inventory_row({"2": 9, "1": "erp-web-01"}, item_type="Computer"),
        )
    )

    assert captured["puts"][0]["url"].endswith("/Ticket/42")
    assert captured["puts"][0]["json"] == {
        "input": {
            "id": 42,
            "externalid": "helpdesk-whatsapp-historical-42",
            "requesttypes_id": 3,
            "itilcategories_id": 11,
        }
    }
    assert captured["posts"][0]["url"].endswith("/Item_Ticket/")
    assert captured["posts"][0]["json"] == {
        "input": {
            "itemtype": "Computer",
            "items_id": 9,
            "tickets_id": 42,
        }
    }
    assert result.status == "updated"
    assert result.request_type_name == "Phone"
    assert result.category_name == "Acesso"
    assert result.linked_item_name == "erp-web-01"