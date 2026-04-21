import asyncio

from app.core.config import Settings
from app.schemas.helpdesk import RequesterIdentity, TicketOpenRequest
from app.services.ansible_runner import AnsibleRunnerExecutionResult
from app.services.automation import AutomationService
from app.services.glpi import GLPIClient
from app.services.job_queue import (
    JobQueueService,
    clear_memory_job_queue,
    get_memory_dead_letter_job_queue_items,
    get_memory_job_queue_items,
)
from app.services.operational_store import (
    OperationalStateStore,
    clear_memory_operational_state,
    get_memory_audit_events,
)
from app.workers.automation_worker import AutomationWorker


class FakeAnsibleRunnerClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_playbook(
        self,
        *,
        project_slug: str,
        playbook_name: str,
        extravars: dict[str, object],
    ) -> AnsibleRunnerExecutionResult:
        self.calls.append(
            {
                "project_slug": project_slug,
                "playbook_name": playbook_name,
                "extravars": extravars,
            }
        )
        if project_slug == "ping-localhost":
            assert playbook_name == "ping_localhost.yml"
            assert extravars == {}
            return AnsibleRunnerExecutionResult(
                result_payload={
                    "executor": "ansible-runner",
                    "project_slug": project_slug,
                    "playbook_name": playbook_name,
                    "status": "successful",
                    "rc": 0,
                },
                notes=["playbook homologado executado no fake runner"],
            )

        if project_slug == "ticket-context-probe":
            assert playbook_name == "ticket_context_probe.yml"
            return AnsibleRunnerExecutionResult(
                result_payload={
                    "executor": "ansible-runner",
                    "project_slug": project_slug,
                    "playbook_name": playbook_name,
                    "status": "successful",
                    "rc": 0,
                    "artifact_data": {
                        "helpdesk_ticket_probe": {
                            "ticket_id": extravars["helpdesk_ticket_id"],
                            "context_label": extravars.get("context_label"),
                            "ticket_context": extravars["helpdesk_ticket_context"],
                        }
                    },
                },
                notes=["playbook ticket context executado no fake runner"],
            )

        raise AssertionError(f"Projeto runner inesperado: {project_slug}")


def test_worker_executes_noop_job_and_marks_completion() -> None:
    clear_memory_operational_state()
    clear_memory_job_queue()
    settings = Settings(
        _env_file=None,
        operational_postgres_dsn=None,
        operational_postgres_schema="helpdesk_platform",
        redis_url=None,
    )
    store = OperationalStateStore(settings)
    queue = JobQueueService(settings)
    worker = AutomationWorker(
        operational_store=store,
        job_queue=queue,
        automation_service=AutomationService(GLPIClient(settings)),
        worker_id="test-worker",
    )

    job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id=None,
            automation_name="noop.healthcheck",
            payload_json={
                "request": {
                    "reason": "smoke",
                    "parameters": {"probe_label": "lab"},
                }
            },
        )
    )
    asyncio.run(queue.enqueue_job(job.job_id))
    asyncio.run(
        store.annotate_job_queue(
            job.job_id,
            queue_mode="memory",
            queue_key=queue.queue_key,
        )
    )

    processed = asyncio.run(worker.run_once(timeout_seconds=0))
    completed_job = asyncio.run(store.get_job_request(job.job_id))

    assert processed is True
    assert completed_job is not None
    assert completed_job.execution_status == "completed"
    assert completed_job.payload_json["execution"]["attempt_count"] == 1
    assert completed_job.payload_json["execution"]["max_attempts"] == 3
    assert completed_job.payload_json["execution"]["worker_id"] == "test-worker"
    assert completed_job.payload_json["execution"]["result"]["result"] == "ok"


def test_worker_executes_ansible_runner_job() -> None:
    clear_memory_operational_state()
    clear_memory_job_queue()
    settings = Settings(
        _env_file=None,
        operational_postgres_dsn=None,
        operational_postgres_schema="helpdesk_platform",
        redis_url=None,
    )
    store = OperationalStateStore(settings)
    queue = JobQueueService(settings)
    worker = AutomationWorker(
        operational_store=store,
        job_queue=queue,
        automation_service=AutomationService(
            GLPIClient(settings),
            ansible_runner_client=FakeAnsibleRunnerClient(),
        ),
        worker_id="ansible-worker",
    )

    job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id=None,
            automation_name="ansible.ping_localhost",
            payload_json={"request": {"reason": "runner", "parameters": {}}},
        )
    )
    asyncio.run(queue.enqueue_job(job.job_id))
    asyncio.run(
        store.annotate_job_queue(
            job.job_id,
            queue_mode="memory",
            queue_key=queue.queue_key,
        )
    )

    processed = asyncio.run(worker.run_once(timeout_seconds=0))
    completed_job = asyncio.run(store.get_job_request(job.job_id))

    assert processed is True
    assert completed_job is not None
    assert completed_job.execution_status == "completed"
    assert completed_job.payload_json["execution"]["result"]["executor"] == "ansible-runner"
    assert completed_job.payload_json["execution"]["result"]["project_slug"] == "ping-localhost"


def test_worker_executes_ticket_bound_ansible_runner_job() -> None:
    clear_memory_operational_state()
    clear_memory_job_queue()
    settings = Settings(
        _env_file=None,
        operational_postgres_dsn=None,
        operational_postgres_schema="helpdesk_platform",
        redis_url=None,
    )
    glpi_client = GLPIClient(settings)
    runner = FakeAnsibleRunnerClient()
    store = OperationalStateStore(settings)
    queue = JobQueueService(settings)
    worker = AutomationWorker(
        operational_store=store,
        job_queue=queue,
        automation_service=AutomationService(
            glpi_client,
            ansible_runner_client=runner,
        ),
        worker_id="ticket-context-worker",
    )

    ticket = asyncio.run(
        glpi_client.create_ticket(
            TicketOpenRequest(
                subject="Probe runner com ticket",
                description="Chamado apenas para validar automacao homologada ligada ao ticket.",
                requester=RequesterIdentity(
                    external_id="user-ticket-probe",
                    display_name="Usuario Probe",
                    phone_number="+5511977771111",
                ),
            )
        )
    )
    job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id=ticket.ticket_id,
            automation_name="ansible.ticket_context_probe",
            payload_json={
                "request": {
                    "reason": "ticket probe",
                    "parameters": {"context_label": "triage"},
                }
            },
        )
    )
    asyncio.run(queue.enqueue_job(job.job_id))
    asyncio.run(
        store.annotate_job_queue(
            job.job_id,
            queue_mode="memory",
            queue_key=queue.queue_key,
        )
    )

    processed = asyncio.run(worker.run_once(timeout_seconds=0))
    completed_job = asyncio.run(store.get_job_request(job.job_id))

    assert processed is True
    assert completed_job is not None
    assert completed_job.execution_status == "completed"
    artifact = completed_job.payload_json["execution"]["result"]["artifact_data"]
    assert artifact["helpdesk_ticket_probe"]["ticket_id"] == ticket.ticket_id
    assert artifact["helpdesk_ticket_probe"]["context_label"] == "triage"
    assert artifact["helpdesk_ticket_probe"]["ticket_context"]["integration_mode"] == "mock"
    assert runner.calls[-1]["project_slug"] == "ticket-context-probe"


def test_worker_executes_glpi_ticket_snapshot_job() -> None:
    clear_memory_operational_state()
    clear_memory_job_queue()
    settings = Settings(
        _env_file=None,
        operational_postgres_dsn=None,
        operational_postgres_schema="helpdesk_platform",
        redis_url=None,
    )
    glpi_client = GLPIClient(settings)
    store = OperationalStateStore(settings)
    queue = JobQueueService(settings)
    worker = AutomationWorker(
        operational_store=store,
        job_queue=queue,
        automation_service=AutomationService(glpi_client),
        worker_id="snapshot-worker",
    )

    ticket = asyncio.run(
        glpi_client.create_ticket(
            TicketOpenRequest(
                subject="Erro operacional para snapshot",
                description="Chamado criado apenas para validar a automacao read-only.",
                requester=RequesterIdentity(
                    external_id="user-snapshot",
                    display_name="Usuario Snapshot",
                    phone_number="+5511999990000",
                ),
            )
        )
    )
    job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id=ticket.ticket_id,
            automation_name="glpi.ticket_snapshot",
            payload_json={"request": {"reason": "snapshot", "parameters": {}}},
        )
    )
    asyncio.run(queue.enqueue_job(job.job_id))
    asyncio.run(
        store.annotate_job_queue(
            job.job_id,
            queue_mode="memory",
            queue_key=queue.queue_key,
        )
    )

    processed = asyncio.run(worker.run_once(timeout_seconds=0))
    completed_job = asyncio.run(store.get_job_request(job.job_id))

    assert processed is True
    assert completed_job is not None
    assert completed_job.execution_status == "completed"
    snapshot = completed_job.payload_json["execution"]["result"]["ticket"]
    assert snapshot["ticket_id"] == ticket.ticket_id
    assert snapshot["integration_mode"] == "mock"
    assert "subject" not in snapshot


def test_worker_blocks_job_without_approval_even_if_queue_item_is_injected() -> None:
    clear_memory_operational_state()
    clear_memory_job_queue()
    settings = Settings(
        _env_file=None,
        operational_postgres_dsn=None,
        operational_postgres_schema="helpdesk_platform",
        redis_url=None,
    )
    store = OperationalStateStore(settings)
    queue = JobQueueService(settings)
    worker = AutomationWorker(
        operational_store=store,
        job_queue=queue,
        automation_service=AutomationService(GLPIClient(settings)),
        worker_id="guard-worker",
    )

    job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-pending",
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
                    "reason": "Queue injection attempt",
                    "parameters": {},
                },
            },
            approval_status="pending",
            execution_status="awaiting-approval",
        )
    )
    asyncio.run(queue.enqueue_job(job.job_id))

    processed = asyncio.run(worker.run_once(timeout_seconds=0))
    blocked_job = asyncio.run(store.get_job_request(job.job_id))
    blocked_events = [
        event for event in get_memory_audit_events() if event.event_type == "automation_job_blocked"
    ]

    assert processed is False
    assert blocked_job is not None
    assert blocked_job.approval_status == "pending"
    assert blocked_job.execution_status == "awaiting-approval"
    assert blocked_events
    assert blocked_events[-1].payload_json["job_id"] == job.job_id
    assert blocked_events[-1].payload_json["block_reason"] == "approval_status_not_approved"


def test_worker_schedules_retry_before_dead_lettering() -> None:
    clear_memory_operational_state()
    clear_memory_job_queue()
    settings = Settings(
        _env_file=None,
        operational_postgres_dsn=None,
        operational_postgres_schema="helpdesk_platform",
        redis_url=None,
        automation_worker_max_attempts=2,
        automation_retry_base_seconds=1,
        automation_retry_max_seconds=1,
    )
    store = OperationalStateStore(settings)
    queue = JobQueueService(settings)
    worker = AutomationWorker(
        operational_store=store,
        job_queue=queue,
        automation_service=AutomationService(GLPIClient(settings)),
        worker_id="retry-worker",
    )

    job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-inexistente",
            automation_name="glpi.ticket_snapshot",
            payload_json={"request": {"reason": "retry", "parameters": {}}},
        )
    )
    asyncio.run(queue.enqueue_job(job.job_id))
    asyncio.run(
        store.annotate_job_queue(
            job.job_id,
            queue_mode="memory",
            queue_key=queue.queue_key,
        )
    )

    first_processed = asyncio.run(worker.run_once(timeout_seconds=0))
    retried_job = asyncio.run(store.get_job_request(job.job_id))

    assert first_processed is True
    assert retried_job is not None
    assert retried_job.execution_status == "retry-scheduled"
    assert retried_job.payload_json["execution"]["attempt_count"] == 1
    assert retried_job.payload_json["execution"]["last_error"]["error_type"] == "ResourceNotFoundError"
    assert retried_job.payload_json["execution"]["retry_delay_seconds"] == 1
    assert retried_job.payload_json["execution"]["retry_scheduled_at"] is not None
    retry_events = [
        event
        for event in get_memory_audit_events()
        if event.event_type == "automation_job_retry_scheduled"
    ]
    assert retry_events
    assert retry_events[-1].payload_json["job_id"] == job.job_id
    assert get_memory_job_queue_items() == []
    assert get_memory_dead_letter_job_queue_items() == []

    asyncio.run(asyncio.sleep(1.05))
    second_processed = asyncio.run(worker.run_once(timeout_seconds=0))
    dead_lettered_job = asyncio.run(store.get_job_request(job.job_id))

    assert second_processed is True
    assert dead_lettered_job is not None
    assert dead_lettered_job.execution_status == "dead-letter"
    assert dead_lettered_job.payload_json["execution"]["attempt_count"] == 2
    assert dead_lettered_job.payload_json["execution"]["dead_lettered_at"] is not None
    assert get_memory_job_queue_items() == []
    assert get_memory_dead_letter_job_queue_items() == [job.job_id]


def test_worker_does_not_execute_cancelled_job_left_in_queue() -> None:
    clear_memory_operational_state()
    clear_memory_job_queue()
    settings = Settings(
        _env_file=None,
        operational_postgres_dsn=None,
        operational_postgres_schema="helpdesk_platform",
        redis_url=None,
    )
    store = OperationalStateStore(settings)
    queue = JobQueueService(settings)
    worker = AutomationWorker(
        operational_store=store,
        job_queue=queue,
        automation_service=AutomationService(GLPIClient(settings)),
        worker_id="cancel-guard-worker",
    )

    job = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-cancelled",
            automation_name="noop.healthcheck",
            payload_json={"request": {"reason": "cancelled", "parameters": {}}},
        )
    )
    asyncio.run(
        store.cancel_job_request(
            job.job_id,
            acted_by="supervisor-ana",
            reason="Execucao revogada antes do worker.",
        )
    )
    asyncio.run(queue.enqueue_job(job.job_id))

    processed = asyncio.run(worker.run_once(timeout_seconds=0))
    blocked_job = asyncio.run(store.get_job_request(job.job_id))
    blocked_events = [
        event for event in get_memory_audit_events() if event.event_type == "automation_job_blocked"
    ]

    assert processed is False
    assert blocked_job is not None
    assert blocked_job.execution_status == "cancelled"
    assert blocked_events
    assert blocked_events[-1].payload_json["job_id"] == job.job_id
    assert blocked_events[-1].payload_json["block_reason"] == "execution_status_not_queued"