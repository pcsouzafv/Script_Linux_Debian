import asyncio
from datetime import datetime, timedelta, timezone

import app.services.operational_store as operational_store_module

from app.core.config import Settings
from app.services.operational_store import (
    OperationalSessionRecord,
    OperationalStateStore,
    clear_memory_operational_state,
    get_memory_audit_events,
)


def test_memory_store_persists_session_state_and_audits_transitions() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
        )
    )

    asyncio.run(
        store.save_session(
            OperationalSessionRecord(
                phone_number="+55 (21) 99999-1111",
                requester_display_name="Maria Santos",
                flow_name="user_catalog_intake",
                stage="awaiting_description",
                selected_catalog_code="4",
                transcript=["Nao consigo acessar o ERP desde 08:10"],
            )
        )
    )

    loaded = asyncio.run(store.load_session("5521999991111"))
    assert loaded is not None
    assert loaded.phone_number == "5521999991111"
    assert loaded.flow_name == "user_catalog_intake"
    assert loaded.stage == "awaiting_description"
    assert loaded.transcript == ["Nao consigo acessar o ERP desde 08:10"]

    save_events = [event for event in get_memory_audit_events() if event.event_type == "session_state_saved"]
    assert save_events
    assert save_events[-1].payload_json == {
        "flow_name": "user_catalog_intake",
        "stage": "awaiting_description",
        "selected_catalog_code": "4",
        "ticket_options": 0,
        "transcript_entries": 1,
    }

    asyncio.run(store.delete_session("5521999991111", reason="test_cleanup"))
    assert asyncio.run(store.load_session("5521999991111")) is None

    clear_events = [event for event in get_memory_audit_events() if event.event_type == "session_state_cleared"]
    assert clear_events
    assert clear_events[-1].payload_json["reason"] == "test_cleanup"


def test_memory_store_records_explicit_audit_events() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
        )
    )

    asyncio.run(
        store.record_audit_event(
            event_type="ticket_status_changed",
            actor_external_id="tech-ana",
            actor_role="technician",
            ticket_id="12345",
            source_channel="whatsapp",
            status="solved",
            payload_json={
                "integration_mode": "mock",
                "new_status": "solved",
            },
        )
    )

    events = get_memory_audit_events()
    assert events
    assert events[-1].event_type == "ticket_status_changed"
    assert events[-1].ticket_id == "12345"
    assert events[-1].payload_json == {
        "integration_mode": "mock",
        "new_status": "solved",
    }


def test_memory_store_sanitizes_large_payloads_for_audit_and_jobs() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
            operational_payload_max_depth=3,
            operational_payload_max_list_items=2,
            operational_payload_max_object_keys=3,
            operational_payload_max_string_length=64,
        )
    )

    event = asyncio.run(
        store.record_audit_event(
            event_type="automation_job_completed",
            actor_external_id="ops-ana",
            actor_role="automation-admin",
            ticket_id="GLPI-LOCAL-900",
            source_channel="automation-worker",
            status="completed",
            payload_json={
                "summary": "x" * 200,
                "items": ["a", "b", "c", "d"],
                "nested": {"level1": {"level2": {"level3": "deep"}}},
                "extra": "ignored",
            },
        )
    )

    assert event.payload_json["summary"].endswith("... [truncated]")
    assert event.payload_json["items"] == ["a", "b", "[truncated 2 additional items]"]
    assert event.payload_json["nested"]["level1"]["level2"] == {
        "__truncated__": "max-depth"
    }
    assert event.payload_json["__truncated_keys__"] == 1

    created = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-901",
            automation_name="noop.healthcheck",
            payload_json={
                "request": {
                    "reason": "y" * 200,
                    "parameters": {"probe_label": "lab"},
                    "extra": [1, 2, 3],
                },
                "extra_1": "value-1",
                "extra_2": "value-2",
                "extra_3": "value-3",
            },
        )
    )

    assert created.payload_json["request"]["reason"].endswith("... [truncated]")
    assert created.payload_json["request"]["extra"] == [1, 2, "[truncated 1 additional items]"]
    assert created.payload_json["__truncated_keys__"] == 2


def test_memory_store_purges_expired_audit_events_when_retention_is_enabled() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
            operational_audit_retention_days=1,
        )
    )

    old_event = asyncio.run(
        store.record_audit_event(
            event_type="ticket_opened",
            actor_external_id="user-old",
            actor_role="user",
            ticket_id="OLD-1",
            source_channel="api",
            status="queued-local",
        )
    )
    recent_event = asyncio.run(
        store.record_audit_event(
            event_type="ticket_opened",
            actor_external_id="user-new",
            actor_role="user",
            ticket_id="NEW-1",
            source_channel="api",
            status="queued-local",
        )
    )

    old_event.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    recent_event.created_at = datetime.now(timezone.utc)

    removed = asyncio.run(store.purge_expired_audit_events())

    assert removed == 1
    events = get_memory_audit_events()
    assert len(events) == 1
    assert events[0].event_id == recent_event.event_id


def test_memory_store_purges_expired_terminal_job_requests_when_retention_is_enabled() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
            operational_job_retention_days=1,
        )
    )

    completed_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-111",
            automation_name="noop.healthcheck",
            payload_json={"request": {"reason": "completed retention", "parameters": {}}},
        )
    )
    asyncio.run(
        store.acquire_job_for_execution(
            completed_source.job_id,
            worker_id="retention-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )
    asyncio.run(
        store.finalize_job_execution(
            completed_source.job_id,
            worker_id="retention-worker",
            execution_status="completed",
            result_payload={"result": "ok"},
        )
    )

    rejected_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-112",
            automation_name="glpi.ticket_snapshot",
            payload_json={
                "policy": {
                    "risk_level": "moderate",
                    "approval_mode": "manual",
                    "approval_required": True,
                },
                "approval": {"status": "pending"},
                "request": {"reason": "rejected retention", "parameters": {}},
            },
            approval_status="pending",
            execution_status="awaiting-approval",
        )
    )
    asyncio.run(
        store.reject_job_request(
            rejected_source.job_id,
            acted_by="supervisor-ana",
            reason="Encerrado para limpeza.",
        )
    )

    queued_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-113",
            automation_name="noop.healthcheck",
            payload_json={"request": {"reason": "queued retention", "parameters": {}}},
        )
    )

    cancelled_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-114",
            automation_name="noop.healthcheck",
            payload_json={"request": {"reason": "cancelled retention", "parameters": {}}},
        )
    )
    asyncio.run(
        store.cancel_job_request(
            cancelled_source.job_id,
            acted_by="supervisor-ana",
            reason="Cancelado para limpeza.",
        )
    )

    old_timestamp = datetime.now(timezone.utc) - timedelta(days=3)
    operational_store_module._MEMORY_JOB_REQUESTS[completed_source.job_id].created_at = old_timestamp
    operational_store_module._MEMORY_JOB_REQUESTS[completed_source.job_id].payload_json["execution"][
        "finished_at"
    ] = old_timestamp.isoformat()
    operational_store_module._MEMORY_JOB_REQUESTS[rejected_source.job_id].created_at = old_timestamp
    operational_store_module._MEMORY_JOB_REQUESTS[rejected_source.job_id].payload_json["approval"][
        "updated_at"
    ] = old_timestamp.isoformat()
    operational_store_module._MEMORY_JOB_REQUESTS[cancelled_source.job_id].created_at = old_timestamp
    operational_store_module._MEMORY_JOB_REQUESTS[cancelled_source.job_id].payload_json["execution"][
        "finished_at"
    ] = old_timestamp.isoformat()
    operational_store_module._MEMORY_JOB_REQUESTS[queued_source.job_id].created_at = old_timestamp

    removed = asyncio.run(store.purge_expired_job_requests())

    assert removed == 3
    assert asyncio.run(store.get_job_request(completed_source.job_id)) is None
    assert asyncio.run(store.get_job_request(rejected_source.job_id)) is None
    assert asyncio.run(store.get_job_request(cancelled_source.job_id)) is None
    assert asyncio.run(store.get_job_request(queued_source.job_id)) is not None


def test_memory_store_expires_stale_pending_job_requests_and_audits_event() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
            automation_approval_timeout_minutes=1,
        )
    )

    pending_job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-120",
            automation_name="glpi.ticket_snapshot",
            payload_json={
                "policy": {
                    "risk_level": "moderate",
                    "approval_mode": "manual",
                    "approval_required": True,
                },
                "approval": {
                    "status": "pending",
                },
                "request": {
                    "reason": "Aguardando revisao manual",
                    "parameters": {},
                },
            },
            approval_status="pending",
            execution_status="awaiting-approval",
        )
    )

    stale_timestamp = datetime.now(timezone.utc) - timedelta(minutes=5)
    operational_store_module._MEMORY_JOB_REQUESTS[pending_job.job_id].created_at = stale_timestamp

    expired_jobs = asyncio.run(store.expire_stale_pending_job_requests())
    loaded = asyncio.run(store.get_job_request(pending_job.job_id))

    assert [job.job_id for job in expired_jobs] == [pending_job.job_id]
    assert loaded is not None
    assert loaded.approval_status == "rejected"
    assert loaded.execution_status == "rejected"
    assert loaded.payload_json["approval"]["acted_by"] == "system-approval-expiration"
    assert "expirada automaticamente" in loaded.payload_json["approval"]["reason"].lower()
    assert loaded.payload_json["approval"]["expiration_policy"]["timeout_minutes"] == 1

    events = [
        event
        for event in get_memory_audit_events()
        if event.event_type == "automation_job_approval_expired"
    ]
    assert len(events) == 1
    assert events[0].payload_json["job_id"] == pending_job.job_id
    assert events[0].payload_json["approval_timeout_minutes"] == 1


def test_memory_store_persists_and_transitions_job_requests() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
        )
    )

    created = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-1",
            automation_name="noop.healthcheck",
            payload_json={
                "request": {
                    "reason": "smoke test",
                    "parameters": {"probe_label": "lab"},
                }
            },
        )
    )

    annotated = asyncio.run(
        store.annotate_job_queue(
            created.job_id,
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )
    acquired = asyncio.run(
        store.acquire_job_for_execution(
            created.job_id,
            worker_id="test-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )
    completed = asyncio.run(
        store.finalize_job_execution(
            created.job_id,
            worker_id="test-worker",
            execution_status="completed",
            result_payload={"result": "ok"},
        )
    )
    loaded = asyncio.run(store.get_job_request(created.job_id))
    listed = asyncio.run(
        store.list_job_requests(
            automation_name="noop.healthcheck",
            execution_status="completed",
        )
    )

    assert annotated is not None
    assert acquired is not None
    assert completed is not None
    assert loaded is not None
    assert loaded.execution_status == "completed"
    assert loaded.payload_json["queue"]["mode"] == "memory"
    assert loaded.payload_json["execution"]["worker_id"] == "test-worker"
    assert loaded.payload_json["execution"]["result"] == {"result": "ok"}
    assert listed.storage_mode == "memory"
    assert [job.job_id for job in listed.jobs] == [created.job_id]


def test_memory_store_transitions_pending_job_through_approval_and_rejection() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
        )
    )

    pending_job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-42",
            automation_name="ansible.ticket_context_probe",
            payload_json={
                "policy": {
                    "risk_level": "moderate",
                    "approval_mode": "manual",
                    "approval_required": True,
                },
                "approval": {
                    "status": "pending",
                },
                "request": {
                    "reason": "Diagnostico manual",
                    "parameters": {"context_label": "manual"},
                },
            },
            approval_status="pending",
            execution_status="awaiting-approval",
        )
    )

    approved_job = asyncio.run(
        store.approve_job_request(
            pending_job.job_id,
            acted_by="supervisor-ana",
            reason_code="read_only_diagnostic_authorized",
            reason="Diagnostico read-only autorizado.",
        )
    )
    listed_approved = asyncio.run(
        store.list_job_requests(
            approval_status="approved",
            execution_status="queued",
        )
    )

    assert approved_job is not None
    assert approved_job.approval_status == "approved"
    assert approved_job.execution_status == "queued"
    assert approved_job.payload_json["approval"]["acted_by"] == "supervisor-ana"
    assert approved_job.payload_json["approval"]["reason_code"] == "read_only_diagnostic_authorized"
    assert approved_job.payload_json["approval"]["updated_at"] is not None
    assert [job.job_id for job in listed_approved.jobs] == [pending_job.job_id]

    rejected_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-43",
            automation_name="glpi.ticket_snapshot",
            payload_json={
                "policy": {
                    "risk_level": "moderate",
                    "approval_mode": "manual",
                    "approval_required": True,
                },
                "approval": {
                    "status": "pending",
                },
                "request": {
                    "reason": "Snapshot aguardando revisao",
                    "parameters": {},
                },
            },
            approval_status="pending",
            execution_status="awaiting-approval",
        )
    )

    rejected_job = asyncio.run(
        store.reject_job_request(
            rejected_source.job_id,
            acted_by="supervisor-ana",
            reason_code="outside_change_window",
            reason="Ticket fora da janela de atendimento autorizada.",
        )
    )
    listed_rejected = asyncio.run(
        store.list_job_requests(
            approval_status="rejected",
            execution_status="rejected",
        )
    )

    assert rejected_job is not None
    assert rejected_job.approval_status == "rejected"
    assert rejected_job.execution_status == "rejected"
    assert rejected_job.payload_json["approval"]["reason_code"] == "outside_change_window"
    assert rejected_job.payload_json["approval"]["reason"] == "Ticket fora da janela de atendimento autorizada."
    assert [job.job_id for job in listed_rejected.jobs] == [rejected_source.job_id]


def test_memory_store_cancels_approved_job_before_execution() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
        )
    )

    queued_job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-44",
            automation_name="noop.healthcheck",
            payload_json={
                "request": {"reason": "Cancelamento preventivo", "parameters": {}},
            },
        )
    )

    cancelled_job = asyncio.run(
        store.cancel_job_request(
            queued_job.job_id,
            acted_by="supervisor-ana",
            reason_code="change_revoked",
            reason="Mudanca revogada antes da execucao.",
        )
    )
    listed_cancelled = asyncio.run(
        store.list_job_requests(
            approval_status="approved",
            execution_status="cancelled",
        )
    )

    assert cancelled_job is not None
    assert cancelled_job.approval_status == "approved"
    assert cancelled_job.execution_status == "cancelled"
    assert cancelled_job.payload_json["execution"]["cancellation"]["acted_by"] == "supervisor-ana"
    assert cancelled_job.payload_json["execution"]["cancellation"]["reason_code"] == "change_revoked"
    assert cancelled_job.payload_json["execution"]["cancellation"]["reason"] == "Mudanca revogada antes da execucao."
    assert cancelled_job.payload_json["execution"]["cancellation"]["cancelled_at"] is not None
    assert [job.job_id for job in listed_cancelled.jobs] == [queued_job.job_id]


def test_memory_store_acquires_due_retry_job_only_after_schedule() -> None:
    clear_memory_operational_state()
    store = OperationalStateStore(
        Settings(
            _env_file=None,
            operational_postgres_dsn=None,
            operational_postgres_schema="helpdesk_platform",
        )
    )

    future_job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-77",
            automation_name="glpi.ticket_snapshot",
            payload_json={
                "request": {
                    "reason": "Retry controlado",
                    "parameters": {},
                }
            },
        )
    )
    acquired = asyncio.run(
        store.acquire_job_for_execution(
            future_job.job_id,
            worker_id="retry-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )

    assert acquired is not None

    scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    scheduled_job = asyncio.run(
        store.mark_job_for_retry(
            future_job.job_id,
            worker_id="retry-worker",
            error_type="ResourceNotFoundError",
            error_message="Ticket nao encontrado.",
            retry_scheduled_at=scheduled_at,
            retry_delay_seconds=30,
        )
    )

    assert scheduled_job is not None
    assert scheduled_job.execution_status == "retry-scheduled"
    assert scheduled_job.payload_json["execution"]["retry_scheduled_at"] == scheduled_at.isoformat()
    assert scheduled_job.payload_json["execution"]["retry_delay_seconds"] == 30

    not_due_job = asyncio.run(store.acquire_due_retry_job(worker_id="retry-worker-2"))
    assert not_due_job is None

    due_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-78",
            automation_name="glpi.ticket_snapshot",
            payload_json={
                "request": {
                    "reason": "Retry vencido",
                    "parameters": {},
                }
            },
        )
    )
    due_acquired = asyncio.run(
        store.acquire_job_for_execution(
            due_source.job_id,
            worker_id="retry-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )

    assert due_acquired is not None

    due_job = asyncio.run(
        store.mark_job_for_retry(
            due_source.job_id,
            worker_id="retry-worker",
            error_type="ResourceNotFoundError",
            error_message="Ticket continua indisponivel.",
            retry_scheduled_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            retry_delay_seconds=1,
        )
    )

    assert due_job is not None
    assert due_job.execution_status == "retry-scheduled"

    reacquired = asyncio.run(store.acquire_due_retry_job(worker_id="retry-worker-2"))

    assert reacquired is not None
    assert reacquired.execution_status == "running"
    assert reacquired.payload_json["execution"]["attempt_count"] == 2
    assert reacquired.payload_json["execution"]["retry_scheduled_at"] is None
    assert reacquired.payload_json["execution"]["retry_delay_seconds"] is None
    assert reacquired.payload_json["queue"]["delivery_mode"] == "scheduled-retry"