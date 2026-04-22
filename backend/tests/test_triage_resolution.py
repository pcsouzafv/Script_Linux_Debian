import asyncio

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services.ticket_analytics_store import (
    TicketAnalyticsSnapshotRecord,
    TicketAnalyticsStore,
)


client = TestClient(app)
client.headers.update({"X-Helpdesk-API-Key": "test-api-token"})


def _seed_snapshot(
    *,
    ticket_id: str,
    status: str,
    priority: str,
    category_name: str,
    service_name: str,
    routed_to: str,
    asset_name: str | None = None,
) -> None:
    store = TicketAnalyticsStore(get_settings())
    asyncio.run(
        store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id=ticket_id,
                subject=f"Historico {ticket_id}",
                description="Snapshot sintetico para enriquecer a triagem.",
                status=status,
                priority=priority,
                requester_glpi_user_id=101,
                assigned_glpi_user_id=201,
                external_id=f"helpdesk-history-{ticket_id}",
                request_type_id=3,
                request_type_name="Phone",
                category_id=1,
                category_name=category_name,
                asset_name=asset_name,
                service_name=service_name,
                source_channel="whatsapp",
                routed_to=routed_to,
                correlation_event_count=1,
            )
        )
    )


def test_triage_endpoint_enriches_resolution_hints_with_analytics_history() -> None:
    _seed_snapshot(
        ticket_id="220",
        status="solved",
        priority="high",
        category_name="Acesso",
        service_name="erp",
        asset_name="erp-web-01",
        routed_to="ServiceDesk-Acessos",
    )
    _seed_snapshot(
        ticket_id="219",
        status="processing",
        priority="medium",
        category_name="Acesso",
        service_name="erp",
        asset_name="erp-web-02",
        routed_to="ServiceDesk-Acessos",
    )

    response = client.post(
        "/api/v1/helpdesk/triage",
        json={
            "subject": "Usuarios sem acesso ao ERP",
            "description": "Financeiro relata erro de autenticacao e nenhum usuario consegue entrar no ERP.",
            "service_name": "erp",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_category"] == "acesso"
    assert body["resolution_hints"]
    assert "servico erp" in body["resolution_hints"][0].lower()
    assert body["similar_incidents"]
    assert body["similar_incidents"][0].startswith("Ticket 220")
    assert any("historico analitico" in note.lower() for note in body["notes"])


def test_admin_operational_flow_mentions_resolution_guidance_and_similar_case() -> None:
    _seed_snapshot(
        ticket_id="330",
        status="solved",
        priority="high",
        category_name="Acesso",
        service_name="erp",
        asset_name="erp-auth-01",
        routed_to="ServiceDesk-Acessos",
    )

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511900019999",
            "sender_name": "Ricardo Admin",
            "text": "Usuarios sem acesso ao ERP desde 08:00.",
            "requester_role": "user",
            "service_name": "erp",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "assistant"
    assert body["assistant_result"]["triage"]["resolution_hints"]
    assert body["assistant_result"]["triage"]["similar_incidents"]
    reply_text = body["assistant_result"]["reply_text"]
    assert "Sugestao de resolucao:" in reply_text
    assert "Caso semelhante: Ticket 330" in reply_text