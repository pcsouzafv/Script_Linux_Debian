import asyncio

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_glpi_client, get_helpdesk_orchestrator
from app.main import app
from app.schemas.helpdesk import WhatsAppWebhookProcessingResponse
from app.services.exceptions import ResourceNotFoundError
from app.services.whatsapp import WhatsAppClient


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 201) -> None:
        self._payload = payload
        self.status_code = status_code
        self._request = httpx.Request("POST", "http://localhost")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self._request,
                response=httpx.Response(self.status_code, request=self._request),
            )

    def json(self) -> dict:
        return self._payload


def test_send_text_message_uses_evolution_when_auto_provider_is_configured(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict | None = None, json: dict | None = None):
            calls.append({"url": url, "headers": headers or {}, "json": json or {}})
            return _FakeResponse({"key": {"id": "evo-msg-123"}})

    monkeypatch.setattr("app.services.whatsapp.httpx.AsyncClient", FakeAsyncClient)

    settings = Settings(
        _env_file=None,
        whatsapp_delivery_provider="auto",
        evolution_base_url="http://localhost:8080",
        evolution_api_key="local-api-key",
        evolution_instance_name="helpdeskAutomacao",
    )
    client = WhatsAppClient(settings)

    result = asyncio.run(client.send_text_message("+55 (21) 99777-5269", "Teste Evolution"))

    assert result.status == "sent"
    assert result.mode == "evolution"
    assert result.provider_message_id == "evo-msg-123"
    assert calls == [
        {
            "url": "http://localhost:8080/message/sendText/helpdeskAutomacao",
            "headers": {
                "apikey": "local-api-key",
                "Content-Type": "application/json",
            },
            "json": {
                "number": "5521997775269",
                "text": "Teste Evolution",
            },
        }
    ]


def test_normalize_evolution_webhook_payload_extracts_incoming_text() -> None:
    client = WhatsAppClient(Settings(_env_file=None))
    payload = {
        "event": "MESSAGES_UPSERT",
        "instance": "helpdeskAutomacao",
        "data": {
            "key": {
                "remoteJid": "5521997775269@s.whatsapp.net",
                "fromMe": False,
                "id": "ABCD1234",
            },
            "pushName": "Maria Santos",
            "message": {
                "extendedTextMessage": {
                    "text": "Estou sem acesso ao GLPI",
                }
            },
            "messageType": "extendedTextMessage",
        },
    }

    messages, ignored_events = client.normalize_evolution_webhook_payload(payload)

    assert ignored_events == []
    assert len(messages) == 1
    assert messages[0].sender_phone == "5521997775269"
    assert messages[0].sender_name == "Maria Santos"
    assert messages[0].text == "Estou sem acesso ao GLPI"
    assert messages[0].external_message_id == "ABCD1234"


def test_normalize_evolution_webhook_payload_maps_configured_lid_to_glpi_phone() -> None:
    settings = Settings(
        _env_file=None,
        evolution_lid_phone_map={"220095666237694@lid": "+5521972008679"},
    )
    client = WhatsAppClient(settings)
    payload = {
        "event": "MESSAGES_UPSERT",
        "data": {
            "key": {
                "remoteJid": "220095666237694@lid",
                "fromMe": False,
                "id": "EVO-LID-123",
            },
            "pushName": "Ricardo Santana",
            "message": {"conversation": "/me"},
            "messageType": "conversation",
        },
    }

    messages, ignored_events = client.normalize_evolution_webhook_payload(payload)

    assert ignored_events == []
    assert len(messages) == 1
    assert messages[0].sender_phone == "+5521972008679"
    assert messages[0].sender_name == "Ricardo Santana"
    assert messages[0].text == "/me"
    assert messages[0].external_message_id == "EVO-LID-123"


def test_normalize_evolution_webhook_payload_ignores_unmapped_lid() -> None:
    client = WhatsAppClient(Settings(_env_file=None))
    payload = {
        "event": "MESSAGES_UPSERT",
        "data": {
            "key": {
                "remoteJid": "220095666237694@lid",
                "fromMe": False,
                "id": "EVO-LID-UNKNOWN",
            },
            "pushName": "Ricardo Santana",
            "message": {"conversation": "/me"},
            "messageType": "conversation",
        },
    }

    messages, ignored_events = client.normalize_evolution_webhook_payload(payload)

    assert messages == []
    assert ignored_events == [
        "Mensagem ignorada: JID 220095666237694@lid não pôde ser convertido em número."
    ]


def test_receive_evolution_webhook_processes_normalized_message() -> None:
    test_client = TestClient(app)

    class FakeOrchestrator:
        def __init__(self) -> None:
            self.messages = []
            self.ignored_events = []

        async def process_whatsapp_webhook_messages(self, messages, ignored_events):
            self.messages = messages
            self.ignored_events = ignored_events
            return WhatsAppWebhookProcessingResponse(
                processed_messages=len(messages),
                interactions=[],
                ignored_events=ignored_events,
                integration_mode="mock",
            )

    fake_orchestrator = FakeOrchestrator()
    app.dependency_overrides[get_helpdesk_orchestrator] = lambda: fake_orchestrator

    settings = get_settings()
    original_secret = settings.evolution_webhook_secret
    settings.evolution_webhook_secret = "segredo-evolution"

    payload = {
        "event": "MESSAGES_UPSERT",
        "data": {
            "key": {
                "remoteJid": "5521972008679@s.whatsapp.net",
                "fromMe": False,
                "id": "EVO-555",
            },
            "pushName": "Paula Almeida",
            "message": {"conversation": "/me"},
            "messageType": "conversation",
        },
    }

    try:
        response = test_client.post(
            "/api/v1/webhooks/whatsapp/evolution",
            json=payload,
            headers={"X-Evolution-Webhook-Secret": "segredo-evolution"},
        )
    finally:
        settings.evolution_webhook_secret = original_secret
        app.dependency_overrides.pop(get_helpdesk_orchestrator, None)

    assert response.status_code == 202
    assert response.json()["processed_messages"] == 1
    assert len(fake_orchestrator.messages) == 1
    assert fake_orchestrator.messages[0].sender_phone == "5521972008679"
    assert fake_orchestrator.messages[0].sender_name == "Paula Almeida"
    assert fake_orchestrator.messages[0].text == "/me"
    assert fake_orchestrator.messages[0].external_message_id == "EVO-555"


def test_receive_evolution_webhook_rejects_invalid_secret() -> None:
    test_client = TestClient(app)
    settings = get_settings()
    original_secret = settings.evolution_webhook_secret
    settings.evolution_webhook_secret = "segredo-evolution"

    try:
        response = test_client.post(
            "/api/v1/webhooks/whatsapp/evolution",
            json={"event": "MESSAGES_UPSERT", "data": {}},
            headers={"X-Evolution-Webhook-Secret": "segredo-invalido"},
        )
    finally:
        settings.evolution_webhook_secret = original_secret

    assert response.status_code == 403
    assert "evolution" in response.json()["detail"].lower()


def test_receive_evolution_webhook_accepts_secret_query_fallback() -> None:
    test_client = TestClient(app)

    class FakeOrchestrator:
        async def process_whatsapp_webhook_messages(self, messages, ignored_events):
            return WhatsAppWebhookProcessingResponse(
                processed_messages=len(messages),
                interactions=[],
                ignored_events=ignored_events,
                integration_mode="mock",
            )

    app.dependency_overrides[get_helpdesk_orchestrator] = lambda: FakeOrchestrator()

    settings = get_settings()
    original_secret = settings.evolution_webhook_secret
    settings.evolution_webhook_secret = "segredo-evolution"

    try:
        response = test_client.post(
            "/api/v1/webhooks/whatsapp/evolution?secret=segredo-evolution",
            json={
                "event": "MESSAGES_UPSERT",
                "data": {
                    "key": {
                        "remoteJid": "5521972008679@s.whatsapp.net",
                        "fromMe": False,
                        "id": "EVO-QUERY-555",
                    },
                    "pushName": "Paula Almeida",
                    "message": {"conversation": "/me"},
                    "messageType": "conversation",
                },
            },
        )
    finally:
        settings.evolution_webhook_secret = original_secret
        app.dependency_overrides.pop(get_helpdesk_orchestrator, None)

    assert response.status_code == 202
    assert response.json()["processed_messages"] == 1


def test_receive_evolution_webhook_acks_unknown_glpi_identity_without_reply() -> None:
    test_client = TestClient(app)

    class FakeGLPIClientRejectUnknownPhone:
        configured = True

        async def find_user_by_phone(self, phone_number: str):
            raise ResourceNotFoundError(
                f"Nenhum usuario GLPI ativo encontrado para numero {phone_number}."
            )

    settings = get_settings()
    original_secret = settings.evolution_webhook_secret
    original_identity_provider = settings.identity_provider
    settings.evolution_webhook_secret = "segredo-evolution"
    settings.identity_provider = "glpi"
    app.dependency_overrides[get_glpi_client] = lambda: FakeGLPIClientRejectUnknownPhone()

    try:
        response = test_client.post(
            "/api/v1/webhooks/whatsapp/evolution?secret=segredo-evolution",
            json={
                "event": "MESSAGES_UPSERT",
                "data": {
                    "key": {
                        "remoteJid": "5511000000000@s.whatsapp.net",
                        "fromMe": False,
                        "id": "EVO-UNKNOWN-555",
                    },
                    "pushName": "Numero Desconhecido",
                    "message": {"conversation": "Quero abrir um chamado"},
                    "messageType": "conversation",
                },
            },
        )
    finally:
        settings.evolution_webhook_secret = original_secret
        settings.identity_provider = original_identity_provider
        app.dependency_overrides.pop(get_glpi_client, None)

    assert response.status_code == 202
    body = response.json()
    assert body["processed_messages"] == 1
    assert body["interactions"] == []
    assert body["integration_mode"] == "noop"
    assert "identidade autorizada" in body["ignored_events"][0]
