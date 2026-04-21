import asyncio

from app.services.glpi import GLPIResolvedCategory, GLPIResolvedInventoryItem, GLPITicketAnalyticsDetails
from app.services.glpi_backfill import GLPIHistoricalBackfillService
from app.services.operational_store import AuditEventListResult, AuditEventRecord


class _FakeGLPIClient:
    def __init__(self, details_by_id: dict[str, GLPITicketAnalyticsDetails]) -> None:
        self.details_by_id = details_by_id
        self.applied: list[dict[str, object]] = []

    async def list_ticket_ids(self, *, limit: int = 20, offset: int = 0) -> list[str]:
        ticket_ids = list(self.details_by_id)
        return ticket_ids[offset : offset + limit]

    async def get_ticket_analytics_details(self, ticket_id: str) -> GLPITicketAnalyticsDetails:
        return self.details_by_id[ticket_id]

    async def resolve_category_by_name(self, category_name: str) -> GLPIResolvedCategory | None:
        if category_name.strip().lower() == "acesso":
            return GLPIResolvedCategory(category_id=11, name="Acesso")
        return None

    async def resolve_inventory_item_by_name(self, asset_name: str) -> GLPIResolvedInventoryItem | None:
        if asset_name == "erp-web-01":
            return GLPIResolvedInventoryItem(item_type="Computer", item_id=9, name="erp-web-01")
        return None

    async def apply_ticket_analytics_patch(self, ticket_id: str, **kwargs):
        self.applied.append({"ticket_id": ticket_id, **kwargs})

        class _Result:
            status = "updated"
            mode = "live"
            notes = ["Campos analíticos atualizados com sucesso no GLPI."]
            external_id = kwargs.get("external_id")
            request_type_id = kwargs.get("request_type_id")
            request_type_name = "Phone" if kwargs.get("request_type_id") == 3 else "Direct"
            category_id = kwargs.get("category_id")
            category_name = kwargs.get("category_name")
            linked_item_type = getattr(kwargs.get("linked_item"), "item_type", None)
            linked_item_id = getattr(kwargs.get("linked_item"), "item_id", None)
            linked_item_name = getattr(kwargs.get("linked_item"), "name", None)

        return _Result()


class _FakeOperationalStore:
    def __init__(self, event_by_ticket: dict[str, AuditEventRecord | None]) -> None:
        self.event_by_ticket = event_by_ticket
        self.recorded_events: list[dict[str, object]] = []

    async def list_audit_events(self, *, limit: int, event_type: str, ticket_id: str):
        event = self.event_by_ticket.get(ticket_id)
        return AuditEventListResult(
            events=[event] if event is not None else [],
            storage_mode="postgres",
            retention_days=30,
        )

    async def record_audit_event(self, **kwargs):
        self.recorded_events.append(kwargs)


class _FakeTriageAgent:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def triage(self, payload):
        self.calls.append(payload)

        class _Response:
            resolved_category = "acesso"

        return _Response()


def test_backfill_uses_operational_audit_before_rules() -> None:
    details = GLPITicketAnalyticsDetails(
        ticket_id="20",
        subject="WhatsApp: Não consigo acessar o ERP",
        description="Origem: WhatsApp\nResumo informado: Não consigo acessar o ERP desde 08:10.",
        status="new",
        priority="high",
        updated_at="2026-04-20T10:00:00+00:00",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=None,
        external_id=None,
        request_type_id=1,
        request_type_name=None,
        category_id=None,
        category_name=None,
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
            "category": "acesso",
            "asset_name": "erp-web-01",
        },
    )
    triage_agent = _FakeTriageAgent()
    service = GLPIHistoricalBackfillService(
        glpi_client=_FakeGLPIClient({"20": details}),
        operational_store=_FakeOperationalStore({"20": audit_event}),
        triage_agent=triage_agent,
    )

    summary = asyncio.run(service.backfill_missing_analytics(ticket_ids=["20"], dry_run=True))

    assert summary.dry_run_count == 1
    decision = summary.results[0]
    assert decision.ticket_id == "20"
    assert decision.external_id == "helpdesk-whatsapp-historical-20"
    assert decision.request_type_id == 3
    assert decision.category_id == 11
    assert decision.linked_item_name == "erp-web-01"
    assert triage_agent.calls == []


def test_backfill_falls_back_to_rules_when_audit_is_missing() -> None:
    details = GLPITicketAnalyticsDetails(
        ticket_id="22",
        subject="WhatsApp: Estou sem acesso ao GLPI desde cedo",
        description="Origem: WhatsApp\nResumo informado: Estou sem acesso ao GLPI desde cedo e preciso validar meu perfil.",
        status="new",
        priority="medium",
        updated_at="2026-04-20T11:00:00+00:00",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=None,
        external_id=None,
        request_type_id=1,
        request_type_name=None,
        category_id=0,
        category_name=None,
        mode="live",
        notes=[],
    )
    triage_agent = _FakeTriageAgent()
    service = GLPIHistoricalBackfillService(
        glpi_client=_FakeGLPIClient({"22": details}),
        operational_store=_FakeOperationalStore({"22": None}),
        triage_agent=triage_agent,
    )

    summary = asyncio.run(service.backfill_missing_analytics(ticket_ids=["22"], dry_run=True))

    assert summary.dry_run_count == 1
    decision = summary.results[0]
    assert decision.external_id == "helpdesk-whatsapp-historical-22"
    assert decision.request_type_id == 3
    assert decision.category_id == 11
    assert len(triage_agent.calls) == 1


def test_backfill_applies_patch_and_records_audit() -> None:
    details = GLPITicketAnalyticsDetails(
        ticket_id="20",
        subject="WhatsApp: Não consigo acessar o ERP",
        description="Origem: WhatsApp\nResumo informado: Não consigo acessar o ERP desde 08:10.",
        status="new",
        priority="high",
        updated_at="2026-04-20T10:00:00+00:00",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=None,
        external_id=None,
        request_type_id=1,
        request_type_name=None,
        category_id=None,
        category_name=None,
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
            "category": "acesso",
            "asset_name": "erp-web-01",
        },
    )
    glpi_client = _FakeGLPIClient({"20": details})
    operational_store = _FakeOperationalStore({"20": audit_event})
    service = GLPIHistoricalBackfillService(
        glpi_client=glpi_client,
        operational_store=operational_store,
        triage_agent=_FakeTriageAgent(),
    )

    summary = asyncio.run(service.backfill_missing_analytics(ticket_ids=["20"], dry_run=False))

    assert summary.updated_count == 1
    assert glpi_client.applied[0]["ticket_id"] == "20"
    assert glpi_client.applied[0]["external_id"] == "helpdesk-whatsapp-historical-20"
    assert glpi_client.applied[0]["request_type_id"] == 3
    assert glpi_client.applied[0]["category_id"] == 11
    assert operational_store.recorded_events[0]["event_type"] == "ticket_analytics_backfilled"


def test_backfill_skips_when_only_asset_candidate_exists() -> None:
    details = GLPITicketAnalyticsDetails(
        ticket_id="16",
        subject="Falha de autenticação no portal corporativo",
        description="Servidor relacionado: auth-01.",
        status="processing",
        priority="high",
        updated_at="2026-04-20T12:00:00+00:00",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=4,
        external_id="lab-ticket-auth",
        request_type_id=4,
        request_type_name="Direct",
        category_id=1,
        category_name="Acesso",
        mode="live",
        notes=[],
    )
    glpi_client = _FakeGLPIClient({"16": details})
    service = GLPIHistoricalBackfillService(
        glpi_client=glpi_client,
        operational_store=_FakeOperationalStore({"16": None}),
        triage_agent=_FakeTriageAgent(),
    )

    summary = asyncio.run(service.backfill_missing_analytics(ticket_ids=["16"], dry_run=True))

    assert summary.skipped_count == 1
    assert summary.results[0].status == "skipped"


def test_backfill_skips_closed_ticket_without_error_on_apply() -> None:
    details = GLPITicketAnalyticsDetails(
        ticket_id="23",
        subject="WhatsApp: Estou sem acesso ao GLPI",
        description="Origem: WhatsApp\nResumo informado: Estou sem acesso ao GLPI.",
        status="closed",
        priority="medium",
        updated_at="2026-04-20T13:00:00+00:00",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=None,
        external_id=None,
        request_type_id=1,
        request_type_name=None,
        category_id=1,
        category_name="Acesso",
        mode="live",
        notes=[],
    )
    glpi_client = _FakeGLPIClient({"23": details})
    service = GLPIHistoricalBackfillService(
        glpi_client=glpi_client,
        operational_store=_FakeOperationalStore({"23": None}),
        triage_agent=_FakeTriageAgent(),
    )

    summary = asyncio.run(service.backfill_missing_analytics(ticket_ids=["23"], dry_run=False))

    assert summary.error_count == 0
    assert summary.skipped_count == 1
    assert summary.results[0].status == "skipped"
    assert glpi_client.applied == []