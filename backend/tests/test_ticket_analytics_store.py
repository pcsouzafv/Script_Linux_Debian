import asyncio
from datetime import datetime, timedelta, timezone

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


def test_memory_snapshot_store_summarizes_operational_ticket_metrics() -> None:
    store = TicketAnalyticsStore(Settings(_env_file=None, operational_postgres_dsn=None))

    asyncio.run(
        store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id="20",
                subject="WhatsApp: ERP indisponivel",
                description="Origem: WhatsApp",
                status="new",
                priority="critical",
                requester_glpi_user_id=7,
                assigned_glpi_user_id=None,
                external_id="helpdesk-whatsapp-historical-20",
                request_type_id=3,
                request_type_name="Phone",
                category_id=1,
                category_name="Infra",
                asset_name="erp-web-01",
                service_name="erp",
                source_channel="whatsapp",
                routed_to="Infraestrutura-N1",
                correlation_event_count=2,
            )
        )
    )
    asyncio.run(
        store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id="21",
                subject="API: Liberar acesso ao ERP",
                description="Origem: API",
                status="processing",
                priority="high",
                requester_glpi_user_id=8,
                assigned_glpi_user_id=101,
                external_id="helpdesk-api-21",
                request_type_id=1,
                request_type_name="Direct",
                category_id=2,
                category_name="Acesso",
                asset_name="erp-auth-01",
                service_name="erp",
                source_channel="api",
                routed_to="ServiceDesk-Acessos",
                correlation_event_count=1,
            )
        )
    )
    asyncio.run(
        store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id="22",
                subject="WhatsApp: problema resolvido",
                description="Origem: WhatsApp",
                status="solved",
                priority="medium",
                requester_glpi_user_id=9,
                assigned_glpi_user_id=102,
                external_id="helpdesk-whatsapp-historical-22",
                request_type_id=3,
                request_type_name="Phone",
                category_id=1,
                category_name="Infra",
                asset_name="erp-batch-01",
                service_name="erp",
                source_channel="whatsapp",
                routed_to="Infraestrutura-N1",
                correlation_event_count=0,
            )
        )
    )

    summary = asyncio.run(store.summarize_snapshots())

    assert summary.storage_mode == "memory"
    assert summary.total_tickets == 3
    assert summary.unresolved_backlog_count == 2
    assert summary.assigned_backlog_count == 1
    assert summary.unassigned_backlog_count == 1
    assert summary.high_priority_backlog_count == 2
    assert summary.resolved_ticket_count == 1
    assert summary.closed_ticket_count == 0
    assert summary.backlog_assignment_coverage_percent == 50.0
    assert summary.resolution_rate_percent == 33.33
    assert summary.average_correlation_event_count == 1.0
    assert summary.status_counts["new"] == 1
    assert summary.status_counts["processing"] == 1
    assert summary.status_counts["solved"] == 1
    assert summary.source_channel_counts["whatsapp"] == 2
    assert summary.source_channel_counts["api"] == 1
    assert summary.category_counts["Infra"] == 2
    assert summary.category_counts["Acesso"] == 1
    assert summary.oldest_backlog_updated_at is not None
    assert summary.newest_snapshot_updated_at is not None


def test_memory_snapshot_store_detects_mass_incident_candidates_by_service() -> None:
    store = TicketAnalyticsStore(Settings(_env_file=None, operational_postgres_dsn=None))
    now = datetime.now(timezone.utc)

    for index, priority in enumerate(("critical", "high", "medium"), start=1):
        asyncio.run(
            store.upsert_snapshot(
                TicketAnalyticsSnapshotRecord(
                    ticket_id=f"70{index}",
                    subject=f"VPN indisponivel para a filial {index}",
                    description="Origem: WhatsApp",
                    status="new",
                    priority=priority,
                    requester_glpi_user_id=20 + index,
                    assigned_glpi_user_id=None if index != 2 else 200,
                    external_id=f"helpdesk-whatsapp-70{index}",
                    request_type_id=3,
                    request_type_name="Phone",
                    category_id=5,
                    category_name="Infra",
                    asset_name=f"vpn-edge-0{index}",
                    service_name="vpn-core",
                    source_channel="whatsapp",
                    routed_to="Infraestrutura-N1",
                    correlation_event_count=index,
                    source_updated_at=now - timedelta(minutes=index * 20),
                )
            )
        )

    summary = asyncio.run(store.summarize_snapshots())

    assert summary.mass_incident_candidate_count == 1
    candidate = summary.mass_incident_candidates[0]
    assert candidate.scope == "service"
    assert candidate.category_name == "Infra"
    assert candidate.routed_to == "Infraestrutura-N1"
    assert candidate.ticket_count == 3
    assert candidate.high_priority_ticket_count == 2
    assert candidate.unassigned_ticket_count == 2
    assert candidate.ticket_ids == ["701", "702", "703"]
    assert candidate.sample_subjects[0] == "VPN indisponivel para a filial 1"
    assert candidate.oldest_ticket_updated_at is not None
    assert candidate.newest_ticket_updated_at is not None
    assert candidate.notes