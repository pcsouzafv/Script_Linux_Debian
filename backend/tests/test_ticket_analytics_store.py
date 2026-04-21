import asyncio

from app.core.config import Settings
from app.services.ticket_analytics_store import TicketAnalyticsSnapshotRecord, TicketAnalyticsStore


def test_memory_snapshot_store_upserts_and_lists_records() -> None:
    store = TicketAnalyticsStore(Settings(_env_file=None, operational_postgres_dsn=None))

    snapshot = TicketAnalyticsSnapshotRecord(
        ticket_id="20",
        subject="WhatsApp: Nao consigo acessar o ERP",
        description="Origem: WhatsApp",
        status="new",
        priority="high",
        requester_glpi_user_id=7,
        assigned_glpi_user_id=None,
        external_id="helpdesk-whatsapp-historical-20",
        request_type_id=3,
        request_type_name="Phone",
        category_id=1,
        category_name="Acesso",
        asset_name="erp-web-01",
        service_name="erp",
        source_channel="whatsapp",
        routed_to="ServiceDesk-Acessos",
        correlation_event_count=0,
        attributes_json={"source": "test"},
    )

    asyncio.run(store.upsert_snapshot(snapshot))
    loaded = asyncio.run(store.get_snapshot("20"))
    listing = asyncio.run(store.list_snapshots(limit=5, category_name="Acesso"))

    assert loaded is not None
    assert loaded.ticket_id == "20"
    assert loaded.external_id == "helpdesk-whatsapp-historical-20"
    assert listing.storage_mode == "memory"
    assert [item.ticket_id for item in listing.snapshots] == ["20"]