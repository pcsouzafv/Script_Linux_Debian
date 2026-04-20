from dataclasses import dataclass

from fastapi.testclient import TestClient

from app.core.dependencies import get_llm_client
from app.main import app


client = TestClient(app)
client.headers.update({"X-Helpdesk-API-Key": "test-api-token"})


@dataclass
class FakeLLMResult:
    provider: str
    model: str
    content: str
    status: str
    notes: list[str]


class FakeLLMClient:
    async def generate_text(
        self,
        user_prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = 400,
        temperature: float | None = None,
    ) -> FakeLLMResult:
        return FakeLLMResult(
            provider="ollama",
            model="llama3.1",
            content=f"echo::{user_prompt}",
            status="completed",
            notes=["Resposta simulada para teste."],
        )


def test_llm_generate_endpoint_returns_generated_content() -> None:
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()

    try:
        response = client.post(
            "/api/v1/helpdesk/ai/generate",
            json={
                "prompt": "Teste do endpoint",
                "system_prompt": "Responda curto",
                "max_tokens": 100,
                "temperature": 0.1,
            },
        )
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "ollama"
    assert body["model"] == "llama3.1"
    assert body["status"] == "completed"
    assert body["content"] == "echo::Teste do endpoint"
