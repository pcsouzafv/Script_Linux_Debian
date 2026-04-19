import asyncio

from app.core.config import Settings
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