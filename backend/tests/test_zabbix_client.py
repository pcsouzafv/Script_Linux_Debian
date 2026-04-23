import asyncio

from app.core.config import Settings
from app.services.zabbix import ZabbixClient


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_reconcile_problem_events_falls_back_to_ack_message_when_manual_close_fails(
    monkeypatch,
) -> None:
    captured_requests: list[dict[str, object]] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict | None = None, json: dict | None = None):
            captured_requests.append(json or {})
            method = (json or {}).get("method")
            if method == "problem.get":
                return _FakeResponse(
                    {
                        "result": [
                            {
                                "eventid": "20427",
                                "name": "VPN down",
                                "severity": "4",
                                "objectid": "55",
                            }
                        ]
                    }
                )
            if method == "event.acknowledge" and (json or {}).get("params", {}).get("action") == 7:
                return _FakeResponse(
                    {
                        "error": {
                            "code": -32602,
                            "message": "Invalid params.",
                            "data": "Cannot close problem: trigger does not allow manual closing.",
                        }
                    }
                )
            if method == "event.acknowledge":
                return _FakeResponse({"result": {"eventids": ["20427"]}})
            raise AssertionError(f"Metodo RPC inesperado no teste: {method}")

    monkeypatch.setattr("app.services.zabbix.httpx.AsyncClient", FakeAsyncClient)

    client = ZabbixClient(
        Settings(
            _env_file=None,
            zabbix_base_url="http://127.0.0.1:8089/api_jsonrpc.php",
            zabbix_api_token="token-123",
        )
    )

    result = asyncio.run(
        client.reconcile_problem_events(
            event_ids=["20427"],
            asset_name="vpn-edge-01",
            service_name="vpn",
            message="Ticket GLPI-LOCAL-123 encerrado no helpdesk.",
            close_problem=True,
        )
    )

    acknowledge_actions = [
        request.get("params", {}).get("action")
        for request in captured_requests
        if request.get("method") == "event.acknowledge"
    ]

    assert result.status == "acknowledged"
    assert result.mode == "live"
    assert result.event_ids == ["20427"]
    assert acknowledge_actions == [7, 6]
    assert any("fechamento manual" in note.lower() for note in result.notes)
