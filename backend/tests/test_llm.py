from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app


client = TestClient(app)
client.headers.update({"X-Helpdesk-API-Key": "test-api-token"})


def test_llm_provider_aliases_are_normalized() -> None:
    assert Settings(llm_provider="openia").llm_provider == "openai"
    assert Settings(llm_provider="anthropic").llm_provider == "claude"
    assert Settings(llm_provider="local").llm_provider == "ollama"


def test_llm_status_endpoint_reports_configured_ollama() -> None:
    settings = get_settings()
    original_values = (
        settings.llm_enabled,
        settings.llm_provider,
        settings.llm_model,
        settings.llm_base_url,
    )

    settings.llm_enabled = True
    settings.llm_provider = "ollama"
    settings.llm_model = "llama3.1"
    settings.llm_base_url = "http://127.0.0.1:11434"

    try:
        response = client.get("/api/v1/helpdesk/ai/status")
    finally:
        (
            settings.llm_enabled,
            settings.llm_provider,
            settings.llm_model,
            settings.llm_base_url,
        ) = original_values

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["provider"] == "ollama"
    assert body["model"] == "llama3.1"
    assert body["status"] == "configured"


def test_llm_status_endpoint_reports_missing_openai_key() -> None:
    settings = get_settings()
    original_values = (
        settings.llm_enabled,
        settings.llm_provider,
        settings.llm_model,
        settings.llm_api_key,
        settings.openai_api_key,
    )

    settings.llm_enabled = True
    settings.llm_provider = "openai"
    settings.llm_model = "gpt-4.1-mini"
    settings.llm_api_key = None
    settings.openai_api_key = None

    try:
        response = client.get("/api/v1/helpdesk/ai/status")
    finally:
        (
            settings.llm_enabled,
            settings.llm_provider,
            settings.llm_model,
            settings.llm_api_key,
            settings.openai_api_key,
        ) = original_values

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["provider"] == "openai"
    assert body["status"] == "incomplete"
    assert "api key" in " ".join(body["notes"]).lower()
