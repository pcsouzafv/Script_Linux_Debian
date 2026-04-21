import asyncio
from datetime import datetime, timezone

from app.services.glpi import GLPITicketAnalyticsDetails
from app.services.glpi_analytics import GLPIAnalyticsSyncService
from app.services.operational_store import AuditEventListResult, AuditEventRecord


class _FakeGLPIClient:
    def __init__(self, details_by_id: dict[str, GLPITicketAnalyticsDetails]) -> None:
        self.details_by_id = details_by_id

    async def list_ticket_ids(self, *, limit: int = 20, offset: int = 0) -> list[str]:
        ticket_ids = list(self.details_by_id)
        return ticket_ids[offset : offset + limit]

    async def get_ticket_analytics_details(self, ticket_id: str) -> GLPITicketAnalyticsDetails:
        return self.details_by_id[ticket_id]


class _FakeOperationalStore:
    def __init__(self, event_by_ticket: dict[str, AuditEventRecord | None]) -> None:
        self.event_by_ticket = event_by_ticket

    async def list_audit_events(self, *, limit: int, event_type: str, ticket_id: str):
        event = self.event_by_ticket.get(ticket_id)
        return AuditEventListResult(
            events=[event] if event is not None else [],
            storage_mode="postgres",
            retention_days=30,
        )


class _FakeAnalyticsStore:
    def __init__(self) -> None:
        self.snapshots = []

    async def upsert_snapshot(self, record):
        self.snapshots.append(record)
        return record


def test_sync_service_builds_snapshot_from_operational_audit() -> None:
    details = GLPITicketAnalyticsDetails(
        ticket_id="20",
        subject="WhatsApp: Nao consigo acessar o ERP",
        description="Origem: WhatsApp\nResumo informado: Nao consigo acessar o ERP.",
        status="new",
        priority="high",
        updated_at="2026-04-21 09:15:00",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=4,
        external_id="helpdesk-whatsapp-historical-20",
        request_type_id=3,
        request_type_name="Phone",
        category_id=1,
        category_name="Acesso",
        mode="live",
        notes=[],
    )
    audit_event = AuditEventRecord(
        event_id="evt-1",
        event_type="ticket_opened",
        actor_external_id="user-maria-santos",
        actor_role="user",
        ticket_id="20",
        source_channel="whatsapp",
        status="created",
        payload_json={
            "asset_name": "erp-web-01",
            "service_name": "erp",
            "routed_to": "ServiceDesk-Acessos",
            "correlation_event_count": 2,
        },
        created_at=datetime(2026, 4, 21, 9, 16, tzinfo=timezone.utc),
    )
    analytics_store = _FakeAnalyticsStore()
    service = GLPIAnalyticsSyncService(
        glpi_client=_FakeGLPIClient({"20": details}),
        operational_store=_FakeOperationalStore({"20": audit_event}),
        analytics_store=analytics_store,
    )

    summary = asyncio.run(service.sync_ticket_snapshots(ticket_ids=["20"]))

    assert summary.synced_count == 1
    snapshot = analytics_store.snapshots[0]
    assert snapshot.ticket_id == "20"
    assert snapshot.source_channel == "whatsapp"
    assert snapshot.asset_name == "erp-web-01"
    assert snapshot.service_name == "erp"
    assert snapshot.routed_to == "ServiceDesk-Acessos"
    assert snapshot.correlation_event_count == 2


def test_sync_service_falls_back_without_operational_audit() -> None:
    details = GLPITicketAnalyticsDetails(
        ticket_id="2",
        subject="ERP indisponivel para homologacao",
        description="Host relacionado: erp-web-01.\nServico relacionado: auth",
        status="new",
        priority="high",
        updated_at="2026-04-21 09:15:00",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=None,
        external_id=None,
        request_type_id=4,
        request_type_name="Direct",
        category_id=6,
        category_name="Infra",
        mode="live",
        notes=[],
    )
    analytics_store = _FakeAnalyticsStore()
    service = GLPIAnalyticsSyncService(
        glpi_client=_FakeGLPIClient({"2": details}),
        operational_store=_FakeOperationalStore({"2": None}),
        analytics_store=analytics_store,
    )

    summary = asyncio.run(service.sync_ticket_snapshots(ticket_ids=["2"]))

    assert summary.synced_count == 1
    snapshot = analytics_store.snapshots[0]
    assert snapshot.ticket_id == "2"
    assert snapshot.source_channel == "api"
    assert snapshot.asset_name == "erp-web-01"
    assert snapshot.service_name == "auth"
    assert snapshot.category_name == "Infra"