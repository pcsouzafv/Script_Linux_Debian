import asyncio
from dataclasses import dataclass

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.dependencies import get_llm_client
from app.main import app
from app.services.glpi import MOCK_TICKET_STORE, MockTicketRecord
from app.services.ticket_analytics_store import TicketAnalyticsSnapshotRecord, TicketAnalyticsStore


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


@dataclass
class FakeLLMStatus:
    enabled: bool
    provider: str
    model: str
    status: str
    base_url: str | None
    notes: list[str]


class FakeResolutionLLMClient:
    last_user_prompt = ""

    def get_status(self) -> FakeLLMStatus:
        return FakeLLMStatus(
            enabled=True,
            provider="ollama",
            model="llama3.1",
            status="configured",
            base_url="http://127.0.0.1:11434",
            notes=["Provider de teste configurado."],
        )

    async def generate_text(
        self,
        user_prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = 400,
        temperature: float | None = None,
    ) -> FakeLLMResult:
        FakeResolutionLLMClient.last_user_prompt = user_prompt
        return FakeLLMResult(
            provider="ollama",
            model="llama3.1",
            content=(
                "resumo: Priorize a validacao do historico ja registrado antes de escalar.\n"
                "acao: Revisar a solution mais recente e repetir a validacao segura no servico afetado.\n"
                "acao: Confirmar com o usuario se o sintoma atual bate com o followup mais recente."
            ),
            status="completed",
            notes=["Resposta simulada para assistencia de resolucao."],
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


def test_ticket_resolution_ai_endpoint_uses_ticket_history_and_llm() -> None:
    store = TicketAnalyticsStore(get_settings())
    asyncio.run(
        store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id="GLPI-LOCAL-777",
                subject="ERP sem acesso",
                description="Snapshot para assistencia de resolucao.",
                status="processing",
                priority="high",
                requester_glpi_user_id=7,
                assigned_glpi_user_id=12,
                external_id="helpdesk-history-777",
                request_type_id=3,
                request_type_name="Phone",
                category_id=1,
                category_name="Acesso",
                asset_name="erp-auth-01",
                service_name="erp",
                source_channel="whatsapp",
                routed_to="ServiceDesk-Acessos",
                correlation_event_count=1,
            )
        )
    )
    MOCK_TICKET_STORE["GLPI-LOCAL-777"] = MockTicketRecord(
        ticket_id="GLPI-LOCAL-777",
        subject="Usuarios sem acesso ao ERP",
        description="Financeiro relata erro de autenticacao para todos os usuarios.",
        status="processing",
        priority="high",
        updated_at="2026-04-21 10:00:00",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=12,
        external_id="helpdesk-history-777",
        request_type_id=3,
        request_type_name="Phone",
        category_id=1,
        category_name="Acesso",
        linked_item_type="Computer",
        linked_item_id=9,
        linked_item_name="erp-auth-01",
        followups=[
            {
                "content": "Usuario confirmou que o erro ocorre apenas no ERP web.",
                "created_at": "2026-04-21 09:30:00",
                "author_glpi_user_id": 12,
            }
        ],
        solutions=[
            {
                "content": "Senha sincronizada no AD e validada com o usuario.",
                "created_at": "2026-04-21 09:45:00",
                "author_glpi_user_id": 12,
            }
        ],
    )

    app.dependency_overrides[get_llm_client] = lambda: FakeResolutionLLMClient()

    try:
        response = client.get("/api/v1/helpdesk/ai/tickets/GLPI-LOCAL-777/resolution")
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == "GLPI-LOCAL-777"
    assert body["summary"] == "Priorize a validacao do historico ja registrado antes de escalar."
    assert body["suggested_actions"]
    assert body["recent_entries"][0]["source"] == "solution"
    assert "Senha sincronizada" in FakeResolutionLLMClient.last_user_prompt
    assert "ERP web" in FakeResolutionLLMClient.last_user_prompt
