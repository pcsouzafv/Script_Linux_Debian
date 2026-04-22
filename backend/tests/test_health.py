import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import app.services.operational_store as operational_store_module

from app.core.config import get_settings
from app.main import app
from app.services.docker_runtime import (
    DockerApplicationRecord,
    DockerContainerRecord,
    DockerRuntimeClient,
    DockerRuntimeSnapshot,
)
from app.services.glpi import MOCK_TICKET_STORE
from app.services.job_queue import JobQueueService
from app.services.operational_store import (
    OperationalSessionRecord,
    OperationalStateStore,
    get_memory_audit_events,
)
from app.services.ticket_analytics_store import TicketAnalyticsSnapshotRecord, TicketAnalyticsStore
from app.services.triage import TriageAgent


client = TestClient(app)
API_HEADERS = {"X-Helpdesk-API-Key": "test-api-token"}
AUDIT_HEADERS = {"X-Helpdesk-Audit-Key": "test-audit-token"}
AUTOMATION_HEADERS = {"X-Helpdesk-Automation-Key": "test-automation-token"}
AUTOMATION_READ_HEADERS = {
    "X-Helpdesk-Automation-Read-Key": "test-automation-read-token"
}
RUNTIME_OVERVIEW_HEADERS = {
    **AUDIT_HEADERS,
    **AUTOMATION_READ_HEADERS,
}
AUTOMATION_APPROVAL_HEADERS = {
    "X-Helpdesk-Automation-Approval-Key": "test-automation-approval-token"
}
client.headers.update(API_HEADERS)


def test_healthcheck_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_protected_route_rejects_missing_api_token() -> None:
    unauthenticated_client = TestClient(app)

    response = unauthenticated_client.post(
        "/api/v1/helpdesk/triage",
        json={
            "subject": "Teste sem token",
            "description": "Validando bloqueio de autenticação nas rotas internas.",
        },
    )

    assert response.status_code == 401
    assert "credenciais" in response.json()["detail"].lower()


def test_protected_route_accepts_previous_api_token_during_rotation() -> None:
    settings = get_settings()
    original_previous = settings.api_access_token_previous
    settings.api_access_token_previous = "test-api-token-previous"

    try:
        response = client.post(
            "/api/v1/helpdesk/triage",
            headers={"X-Helpdesk-API-Key": "test-api-token-previous"},
            json={
                "subject": "Teste com token anterior",
                "description": "Validando a janela de rotacao do token interno geral.",
            },
        )
    finally:
        settings.api_access_token_previous = original_previous

    assert response.status_code == 200


def test_audit_route_rejects_api_token_without_dedicated_audit_token() -> None:
    response = client.get("/api/v1/helpdesk/audit/events?event_type=ticket_opened")

    assert response.status_code == 401
    assert "auditoria administrativa" in response.json()["detail"].lower()


def test_audit_route_accepts_previous_audit_token_during_rotation() -> None:
    settings = get_settings()
    original_previous = settings.audit_access_token_previous
    settings.audit_access_token_previous = "test-audit-token-previous"

    try:
        response = client.get(
            "/api/v1/helpdesk/audit/events?event_type=ticket_opened",
            headers={"X-Helpdesk-Audit-Key": "test-audit-token-previous"},
        )
    finally:
        settings.audit_access_token_previous = original_previous

    assert response.status_code == 200


def test_ticket_operations_summary_route_rejects_api_token_without_dedicated_audit_token() -> None:
    response = client.get("/api/v1/helpdesk/reports/tickets/summary")

    assert response.status_code == 401
    assert "auditoria administrativa" in response.json()["detail"].lower()


def test_ticket_operations_summary_route_returns_backlog_distribution() -> None:
    store = TicketAnalyticsStore(get_settings())
    now = datetime.now(timezone.utc)

    asyncio.run(
        store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id="501",
                subject="WhatsApp: ERP indisponivel",
                description="Origem: WhatsApp",
                status="new",
                priority="critical",
                requester_glpi_user_id=7,
                assigned_glpi_user_id=None,
                external_id="helpdesk-whatsapp-501",
                request_type_id=3,
                request_type_name="Phone",
                category_id=1,
                category_name="Infra",
                asset_name="erp-web-01",
                service_name="erp",
                source_channel="whatsapp",
                routed_to="Infraestrutura-N1",
                correlation_event_count=2,
                source_updated_at=now - timedelta(hours=3),
            )
        )
    )
    asyncio.run(
        store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id="502",
                subject="API: Liberar acesso ao ERP",
                description="Origem: API",
                status="processing",
                priority="high",
                requester_glpi_user_id=8,
                assigned_glpi_user_id=101,
                external_id="helpdesk-api-502",
                request_type_id=1,
                request_type_name="Direct",
                category_id=2,
                category_name="Acesso",
                asset_name="erp-auth-01",
                service_name="erp",
                source_channel="api",
                routed_to="ServiceDesk-Acessos",
                correlation_event_count=1,
                source_updated_at=now - timedelta(hours=1),
            )
        )
    )
    asyncio.run(
        store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id="503",
                subject="WhatsApp: incidente resolvido",
                description="Origem: WhatsApp",
                status="solved",
                priority="medium",
                requester_glpi_user_id=9,
                assigned_glpi_user_id=102,
                external_id="helpdesk-whatsapp-503",
                request_type_id=3,
                request_type_name="Phone",
                category_id=1,
                category_name="Infra",
                asset_name="erp-batch-01",
                service_name="erp",
                source_channel="whatsapp",
                routed_to="Infraestrutura-N1",
                correlation_event_count=0,
                source_updated_at=now,
            )
        )
    )

    response = client.get(
        "/api/v1/helpdesk/reports/tickets/summary",
        headers=AUDIT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["storage_mode"] == "memory"
    assert body["total_tickets"] == 3
    assert body["unresolved_backlog_count"] == 2
    assert body["assigned_backlog_count"] == 1
    assert body["unassigned_backlog_count"] == 1
    assert body["high_priority_backlog_count"] == 2
    assert body["resolved_ticket_count"] == 1
    assert body["closed_ticket_count"] == 0
    assert body["backlog_assignment_coverage_percent"] == 50.0
    assert body["resolution_rate_percent"] == 33.33
    assert body["average_correlation_event_count"] == 1.0
    assert body["status_counts"]["new"] == 1
    assert body["status_counts"]["processing"] == 1
    assert body["status_counts"]["solved"] == 1
    assert body["source_channel_counts"]["whatsapp"] == 2
    assert body["source_channel_counts"]["api"] == 1
    assert body["category_counts"]["Infra"] == 2
    assert body["category_counts"]["Acesso"] == 1
    assert body["routed_to_counts"]["Infraestrutura-N1"] == 2
    assert body["routed_to_counts"]["ServiceDesk-Acessos"] == 1
    assert body["oldest_backlog_updated_at"] is not None
    assert body["newest_snapshot_updated_at"] is not None


def test_ticket_operations_summary_route_returns_mass_incident_candidates() -> None:
    store = TicketAnalyticsStore(get_settings())
    now = datetime.now(timezone.utc)

    for index, priority in enumerate(("critical", "high", "medium"), start=1):
        asyncio.run(
            store.upsert_snapshot(
                TicketAnalyticsSnapshotRecord(
                    ticket_id=f"60{index}",
                    subject=f"ERP fora do ar na operacao {index}",
                    description="Origem: API",
                    status="processing" if index == 2 else "new",
                    priority=priority,
                    requester_glpi_user_id=10 + index,
                    assigned_glpi_user_id=500 + index if index == 2 else None,
                    external_id=f"helpdesk-api-60{index}",
                    request_type_id=1,
                    request_type_name="Direct",
                    category_id=1,
                    category_name="Infra",
                    asset_name=f"erp-node-0{index}",
                    service_name="erp-core",
                    source_channel="api",
                    routed_to="Infraestrutura-N1",
                    correlation_event_count=index,
                    source_updated_at=now - timedelta(minutes=index * 15),
                )
            )
        )

    response = client.get(
        "/api/v1/helpdesk/reports/tickets/summary",
        headers=AUDIT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mass_incident_candidate_count"] == 1
    candidate = body["mass_incident_candidates"][0]
    assert candidate["scope"] == "service"
    assert candidate["category_name"] == "Infra"
    assert candidate["routed_to"] == "Infraestrutura-N1"
    assert candidate["ticket_count"] == 3
    assert candidate["high_priority_ticket_count"] == 2
    assert candidate["unassigned_ticket_count"] == 2
    assert candidate["ticket_ids"] == ["601", "602", "603"]
    assert candidate["sample_subjects"][0] == "ERP fora do ar na operacao 1"
    assert candidate["notes"]


def test_runtime_overview_route_requires_automation_read_scope_alongside_audit_scope() -> None:
    response = client.get(
        "/api/v1/helpdesk/runtime/overview",
        headers=AUDIT_HEADERS,
    )

    assert response.status_code == 401
    assert "leitura administrativa de automação" in response.json()["detail"].lower()


def test_runtime_overview_route_returns_sessions_audit_and_operational_summaries(monkeypatch) -> None:
    settings = get_settings()
    session_store = OperationalStateStore(settings)
    analytics_store = TicketAnalyticsStore(settings)
    now = datetime.now(timezone.utc)

    async def fake_get_runtime_snapshot(self, *, limit: int = 12) -> DockerRuntimeSnapshot:
        return DockerRuntimeSnapshot(
            configured=True,
            status="configured",
            mode="docker-cli",
            binary_path="/usr/bin/docker",
            application_count=2,
            total_containers=3,
            running_count=2,
            exited_count=1,
            restarting_count=0,
            unhealthy_count=1,
            applications=[
                DockerApplicationRecord(
                    application_name="helpdesk-lab",
                    status="running",
                    total_containers=1,
                    running_count=1,
                    unhealthy_count=0,
                    application_services=["glpi"],
                    support_services=[],
                    notes=[],
                ),
                DockerApplicationRecord(
                    application_name="idiomasbr2026",
                    status="running",
                    total_containers=2,
                    running_count=1,
                    unhealthy_count=1,
                    application_services=["backend"],
                    support_services=["redis (cache)"],
                    notes=["Redis/cache detectado como apoio de desempenho do stack."],
                ),
            ],
            containers=[
                DockerContainerRecord(
                    container_id="abc123",
                    name="glpi-app",
                    image="glpi:10",
                    status="Up 2 hours (healthy)",
                    state="running",
                    application_name="helpdesk-lab",
                    service_role="service",
                    health_status="healthy",
                    compose_project="helpdesk-lab",
                    compose_service="glpi",
                    ports="0.0.0.0:8088->80/tcp",
                ),
                DockerContainerRecord(
                    container_id="def456",
                    name="worker-api",
                    image="custom/worker:latest",
                    status="Up 5 minutes (unhealthy)",
                    state="running",
                    application_name="idiomasbr2026",
                    service_role="backend",
                    health_status="unhealthy",
                    compose_project="ops-stack",
                    compose_service="worker",
                    ports=None,
                ),
            ],
            notes=["Monitoramento Docker consultado via CLI local."],
        )

    monkeypatch.setattr(DockerRuntimeClient, "get_runtime_snapshot", fake_get_runtime_snapshot)

    asyncio.run(
        session_store.save_session(
            OperationalSessionRecord(
                phone_number="+5511912345678",
                requester_display_name="Ricardo Runtime",
                flow_name="user_ticket_finalization",
                stage="awaiting_ticket_selection",
                selected_catalog_code="vpn-access",
                transcript=["Quero encerrar o chamado", "Escolha uma opcao"],
                ticket_options=[{"ticket_id": "GLPI-1001", "subject": "VPN"}],
            )
        )
    )
    asyncio.run(
        analytics_store.upsert_snapshot(
            TicketAnalyticsSnapshotRecord(
                ticket_id="701",
                subject="WhatsApp: VPN intermitente",
                description="Origem: WhatsApp",
                status="new",
                priority="high",
                requester_glpi_user_id=31,
                assigned_glpi_user_id=None,
                external_id="helpdesk-whatsapp-701",
                request_type_id=3,
                request_type_name="Phone",
                category_id=1,
                category_name="Rede",
                asset_name="vpn-gateway-01",
                service_name="vpn",
                source_channel="whatsapp",
                routed_to="Infraestrutura-N1",
                correlation_event_count=3,
                source_updated_at=now,
            )
        )
    )
    create_job_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "noop.healthcheck",
            "ticket_id": "701",
            "reason": "Popular overview operacional",
        },
    )

    assert create_job_response.status_code == 202

    response = client.get(
        "/api/v1/helpdesk/runtime/overview",
        headers=RUNTIME_OVERVIEW_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["health"]["status"] == "ok"
    assert body["health"]["api_prefix"] == "/api/v1"
    assert body["glpi"]["status"] == "mock"
    assert body["zabbix"]["status"] == "mock"
    assert body["messaging"]["resolved_delivery_provider"] == "mock"
    assert body["llm"]["status"] == "disabled"
    assert body["operational_store"]["status"] == "memory"
    assert body["operational_store"]["mode"] == "memory"
    assert body["operational_store"]["schema_name"] == settings.operational_postgres_schema
    assert body["queue"]["status"] == "memory"
    assert body["queue"]["mode"] == "memory"
    assert body["queue"]["queue_key"] == "helpdesk:automation:jobs"
    assert body["queue"]["queue_depth"] == 1
    assert body["automation_runner"]["mode"] == "ansible-runner"
    assert body["automation_runner"]["project_count"] >= 1
    assert body["automation_runner"]["catalog_entry_count"] >= 1
    assert body["docker"]["status"] == "configured"
    assert body["docker"]["mode"] == "docker-cli"
    assert body["docker"]["application_count"] == 2
    assert body["docker"]["total_containers"] == 3
    assert body["docker"]["running_count"] == 2
    assert body["docker"]["unhealthy_count"] == 1
    assert body["docker"]["applications"][1]["application_name"] == "idiomasbr2026"
    assert body["docker"]["applications"][1]["support_services"] == ["redis (cache)"]
    assert "desempenho" in body["docker"]["applications"][1]["notes"][0].lower()
    assert body["docker"]["containers"][0]["name"] == "glpi-app"
    assert body["docker"]["containers"][0]["application_name"] == "helpdesk-lab"
    assert body["docker"]["containers"][1]["health_status"] == "unhealthy"
    assert body["sessions"]["storage_mode"] == "memory"
    assert body["sessions"]["total_sessions"] == 1
    assert body["sessions"]["sessions"][0]["phone_number_masked"] == "***5678"
    assert body["sessions"]["sessions"][0]["flow_name"] == "user_ticket_finalization"
    assert body["audit"]["recent_event_count"] >= 1
    assert body["audit"]["event_type_counts"]["session_state_saved"] >= 1
    assert body["ticket_operations"]["total_tickets"] == 1
    assert body["ticket_operations"]["source_channel_counts"]["whatsapp"] == 1
    assert body["automation"]["total_jobs"] == 1


def test_ops_dashboard_page_is_available_for_browser_access() -> None:
    response = client.get("/ops")

    assert response.status_code == 200
    assert "Painel operacional do orquestrador" in response.text
    assert "/api/v1/helpdesk/runtime/overview" in response.text
    assert "Containers Docker" in response.text


def test_automation_route_rejects_api_token_without_dedicated_automation_token() -> None:
    response = client.get("/api/v1/helpdesk/automation/jobs")

    assert response.status_code == 401
    assert "administrativa" in response.json()["detail"].lower()


def test_automation_route_accepts_previous_automation_token_during_rotation() -> None:
    settings = get_settings()
    original_previous = settings.automation_access_token_previous
    settings.automation_access_token_previous = "test-automation-token-previous"

    try:
        response = client.post(
            "/api/v1/helpdesk/automation/jobs",
            headers={"X-Helpdesk-Automation-Key": "test-automation-token-previous"},
            json={
                "requested_by": "ops-ana",
                "automation_name": "noop.healthcheck",
                "reason": "Teste com token anterior de escrita",
            },
        )
    finally:
        settings.automation_access_token_previous = original_previous

    assert response.status_code == 202


def test_automation_job_create_rejects_requested_by_with_spaces() -> None:
    response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops ana",
            "automation_name": "noop.healthcheck",
            "reason": "Validar identificador administrativo",
        },
    )

    assert response.status_code == 422
    assert any(item["loc"][-1] == "requested_by" for item in response.json()["detail"])


def test_automation_read_route_rejects_write_token_when_dedicated_read_token_is_configured() -> None:
    response = client.get(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
    )

    assert response.status_code == 401
    assert "leitura administrativa de automação" in response.json()["detail"].lower()


def test_automation_read_route_accepts_previous_read_token_during_rotation() -> None:
    settings = get_settings()
    original_previous = settings.automation_read_access_token_previous
    settings.automation_read_access_token_previous = "test-automation-read-token-previous"

    try:
        response = client.get(
            "/api/v1/helpdesk/automation/jobs",
            headers={"X-Helpdesk-Automation-Read-Key": "test-automation-read-token-previous"},
        )
    finally:
        settings.automation_read_access_token_previous = original_previous

    assert response.status_code == 200


def test_automation_summary_route_rejects_write_token_when_read_scope_is_required() -> None:
    response = client.get(
        "/api/v1/helpdesk/automation/summary",
        headers=AUTOMATION_HEADERS,
    )

    assert response.status_code == 401
    assert "leitura administrativa de automação" in response.json()["detail"].lower()


def test_automation_approval_route_rejects_automation_token_without_dedicated_approval_token() -> None:
    response = client.post(
        "/api/v1/helpdesk/automation/jobs/non-existent/approve",
        headers=AUTOMATION_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "change_window_validated",
        },
    )

    assert response.status_code == 401
    assert "aprovação administrativa de automação" in response.json()["detail"].lower()


def test_automation_approval_rejects_acted_by_with_spaces() -> None:
    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "noop.healthcheck",
            "reason": "Validar identificador de aprovacao",
        },
    )

    response = client.post(
        f"/api/v1/helpdesk/automation/jobs/{create_response.json()['job_id']}/approve",
        headers=AUTOMATION_APPROVAL_HEADERS,
        json={
            "acted_by": "supervisor ana",
            "reason_code": "change_window_validated",
        },
    )

    assert response.status_code == 422
    assert any(item["loc"][-1] == "acted_by" for item in response.json()["detail"])


def test_automation_approval_route_accepts_previous_approval_token_during_rotation() -> None:
    settings = get_settings()
    original_previous = settings.automation_approval_access_token_previous
    settings.automation_approval_access_token_previous = "test-automation-approval-token-previous"

    try:
        ticket_response = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado aguardando aprovacao por token anterior",
                "description": "Valida a janela de rotacao do token de aprovacao.",
                "requester": {
                    "external_id": "user-approval-rotation",
                    "display_name": "Usuario Approval Rotation",
                    "phone_number": "+5511912207788",
                    "role": "user",
                },
            },
        )
        job_response = client.post(
            "/api/v1/helpdesk/automation/jobs",
            headers=AUTOMATION_HEADERS,
            json={
                "requested_by": "ops-ana",
                "automation_name": "glpi.ticket_snapshot",
                "ticket_id": ticket_response.json()["ticket_id"],
                "reason": "Teste com token anterior de aprovacao",
            },
        )
        job_id = job_response.json()["job_id"]

        response = client.post(
            f"/api/v1/helpdesk/automation/jobs/{job_id}/approve",
            headers={
                "X-Helpdesk-Automation-Approval-Key": "test-automation-approval-token-previous"
            },
            json={
                "acted_by": "supervisor-ana",
                "reason_code": "change_window_validated",
            },
        )
    finally:
        settings.automation_approval_access_token_previous = original_previous

    assert response.status_code == 200


def test_automation_cancel_route_rejects_automation_token_without_dedicated_approval_token() -> None:
    response = client.post(
        "/api/v1/helpdesk/automation/jobs/non-existent/cancel",
        headers=AUTOMATION_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "change_revoked",
        },
    )

    assert response.status_code == 401
    assert "aprovação administrativa de automação" in response.json()["detail"].lower()


def test_create_automation_job_enqueues_and_lists_job() -> None:
    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "noop.healthcheck",
            "reason": "Smoke test da fila interna",
            "parameters": {"probe_label": "lab"},
        },
    )

    assert create_response.status_code == 202
    create_body = create_response.json()
    assert create_body["automation_name"] == "noop.healthcheck"
    assert create_body["risk_level"] == "low"
    assert create_body["approval_mode"] == "auto"
    assert create_body["approval_required"] is False
    assert create_body["approval_status"] == "approved"
    assert create_body["approval_acted_by"] == "system-policy"
    assert create_body["approval_reason_code"] == "policy_auto_approved"
    assert "low-risk" in create_body["approval_reason"]
    assert create_body["approval_updated_at"] is not None
    assert create_body["execution_status"] == "queued"
    assert create_body["attempt_count"] == 0
    assert create_body["max_attempts"] == 3
    assert create_body["last_error"] is None
    assert create_body["queue_mode"] == "memory"
    assert create_body["payload_json"]["request"]["reason"] == "Smoke test da fila interna"

    job_id = create_body["job_id"]
    list_response = client.get(
        "/api/v1/helpdesk/automation/jobs?approval_status=approved&execution_status=queued",
        headers=AUTOMATION_READ_HEADERS,
    )
    detail_response = client.get(
        f"/api/v1/helpdesk/automation/jobs/{job_id}",
        headers=AUTOMATION_READ_HEADERS,
    )

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    list_body = list_response.json()
    assert list_body["storage_mode"] == "memory"
    assert any(job["job_id"] == job_id for job in list_body["jobs"])
    assert detail_response.json()["job_id"] == job_id


def test_automation_summary_route_reports_operational_state() -> None:
    queued_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "noop.healthcheck",
            "reason": "Job enfileirado para resumo operacional",
        },
    )

    assert queued_response.status_code == 202

    ticket_response = client.post(
        "/api/v1/helpdesk/tickets/open",
        json={
            "subject": "Chamado aguardando aprovacao para resumo",
            "description": "Chamado de teste para compor o resumo operacional da fila.",
            "requester": {
                "external_id": "user-summary-probe",
                "display_name": "Usuario Summary Probe",
                "phone_number": "+5511913304455",
                "role": "user",
            },
        },
    )
    ticket_id = ticket_response.json()["ticket_id"]

    pending_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "glpi.ticket_snapshot",
            "ticket_id": ticket_id,
            "reason": "Job manual para aparecer como aguardando aprovacao",
        },
    )

    assert pending_response.status_code == 202

    settings = get_settings()
    store = OperationalStateStore(settings)
    queue_service = JobQueueService(settings)

    retry_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-500",
            automation_name="noop.healthcheck",
            payload_json={"request": {"reason": "Retry para resumo", "parameters": {}}},
        )
    )
    retry_acquired = asyncio.run(
        store.acquire_job_for_execution(
            retry_source.job_id,
            worker_id="summary-retry-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )
    assert retry_acquired is not None
    scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=90)
    retry_scheduled = asyncio.run(
        store.mark_job_for_retry(
            retry_source.job_id,
            worker_id="summary-retry-worker",
            error_type="ValueError",
            error_message="Falha controlada para resumo.",
            retry_scheduled_at=scheduled_at,
            retry_delay_seconds=90,
        )
    )
    assert retry_scheduled is not None

    running_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-501",
            automation_name="noop.healthcheck",
            payload_json={"request": {"reason": "Running para resumo", "parameters": {}}},
        )
    )
    running_job = asyncio.run(
        store.acquire_job_for_execution(
            running_source.job_id,
            worker_id="summary-running-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )
    assert running_job is not None

    dead_letter_source = asyncio.run(
        store.create_job_request(
            requested_by="ops-ana",
            ticket_id="GLPI-LOCAL-502",
            automation_name="noop.healthcheck",
            payload_json={"request": {"reason": "Dead-letter para resumo", "parameters": {}}},
        )
    )
    dead_letter_acquired = asyncio.run(
        store.acquire_job_for_execution(
            dead_letter_source.job_id,
            worker_id="summary-dead-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )
    assert dead_letter_acquired is not None
    dead_letter_job = asyncio.run(
        store.mark_job_dead_letter(
            dead_letter_source.job_id,
            worker_id="summary-dead-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs:dead-letter",
            error_type="RuntimeError",
            error_message="Falha terminal controlada para resumo.",
        )
    )
    assert dead_letter_job is not None
    dead_letter_enqueue = asyncio.run(
        queue_service.enqueue_job(dead_letter_source.job_id, dead_letter=True)
    )
    assert dead_letter_enqueue.queue_mode == "memory"

    summary_response = client.get(
        "/api/v1/helpdesk/automation/summary",
        headers=AUTOMATION_READ_HEADERS,
    )

    assert summary_response.status_code == 200
    body = summary_response.json()
    assert body["storage_mode"] == "memory"
    assert body["queue_mode"] == "memory"
    assert body["approval_timeout_minutes"] == settings.automation_approval_timeout_minutes
    assert body["total_jobs"] == 5
    assert body["approval_status_counts"] == {
        "pending": 1,
        "approved": 4,
        "rejected": 0,
    }
    assert body["execution_status_counts"]["awaiting-approval"] == 1
    assert body["execution_status_counts"]["queued"] == 1
    assert body["execution_status_counts"]["running"] == 1
    assert body["execution_status_counts"]["retry-scheduled"] == 1
    assert body["execution_status_counts"]["dead-letter"] == 1
    assert body["queue_depth"] == 1
    assert body["dead_letter_queue_depth"] == 1
    assert body["oldest_job_created_at"] is not None
    assert body["oldest_pending_approval_started_at"] is not None
    assert body["oldest_pending_approval_expires_at"] is not None
    assert body["oldest_queued_job_created_at"] is not None
    assert body["oldest_running_started_at"] is not None
    assert body["oldest_retry_scheduled_at"] == scheduled_at.isoformat()


def test_ticket_bound_automation_job_requires_ticket_id() -> None:
    response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "ansible.ticket_context_probe",
            "reason": "Probe sem ticket nao deve entrar na fila",
            "parameters": {"context_label": "diagnostico"},
        },
    )

    assert response.status_code == 400
    assert "ticket_id" in response.json()["detail"]


def test_create_ticket_bound_automation_job_accepts_context_label() -> None:
    ticket_response = client.post(
        "/api/v1/helpdesk/tickets/open",
        json={
            "subject": "Chamado para probe runner",
            "description": "Chamado de teste para validar automacao homologada vinculada a ticket.",
            "requester": {
                "external_id": "user-api-probe",
                "display_name": "Usuario API Probe",
                "phone_number": "+5511911102222",
                "role": "user",
            },
        },
    )
    ticket_id = ticket_response.json()["ticket_id"]

    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "ansible.ticket_context_probe",
            "ticket_id": ticket_id,
            "reason": "Probe vinculado ao ticket",
            "parameters": {"context_label": "diagnostico"},
        },
    )

    assert create_response.status_code == 202
    body = create_response.json()
    assert body["automation_name"] == "ansible.ticket_context_probe"
    assert body["ticket_id"] == ticket_id
    assert body["risk_level"] == "moderate"
    assert body["approval_mode"] == "manual"
    assert body["approval_required"] is True
    assert body["approval_status"] == "pending"
    assert body["approval_acted_by"] is None
    assert body["approval_reason_code"] is None
    assert body["approval_reason"] is None
    assert body["approval_updated_at"] is None
    assert body["payload_json"]["request"]["parameters"]["context_label"] == "diagnostico"
    assert body["execution_status"] == "awaiting-approval"
    assert body["queue_mode"] is None

    list_response = client.get(
        "/api/v1/helpdesk/automation/jobs?approval_status=pending&execution_status=awaiting-approval",
        headers=AUTOMATION_READ_HEADERS,
    )

    assert list_response.status_code == 200
    assert any(job["job_id"] == body["job_id"] for job in list_response.json()["jobs"])


def test_approve_ticket_bound_automation_job_enqueues_after_manual_approval() -> None:
    ticket_response = client.post(
        "/api/v1/helpdesk/tickets/open",
        json={
            "subject": "Chamado aguardando aprovacao",
            "description": "Chamado de teste para liberar a automacao homologada apos revisao.",
            "requester": {
                "external_id": "user-approval-probe",
                "display_name": "Usuario Approval Probe",
                "phone_number": "+5511912203344",
                "role": "user",
            },
        },
    )
    ticket_id = ticket_response.json()["ticket_id"]

    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "ansible.ticket_context_probe",
            "ticket_id": ticket_id,
            "reason": "Diagnostico read-only do chamado",
            "parameters": {"context_label": "janela-controlada"},
        },
    )
    job_id = create_response.json()["job_id"]

    approve_response = client.post(
        f"/api/v1/helpdesk/automation/jobs/{job_id}/approve",
        headers=AUTOMATION_APPROVAL_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "read_only_diagnostic_authorized",
        },
    )

    assert approve_response.status_code == 200
    body = approve_response.json()
    assert body["job_id"] == job_id
    assert body["approval_status"] == "approved"
    assert body["approval_acted_by"] == "supervisor-ana"
    assert body["approval_reason_code"] == "read_only_diagnostic_authorized"
    assert body["approval_reason"] == "Diagnostico read-only autorizado."
    assert body["approval_updated_at"] is not None
    assert body["execution_status"] == "queued"
    assert body["queue_mode"] == "memory"


def test_cancel_queued_automation_job_removes_it_from_execution_window() -> None:
    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "noop.healthcheck",
            "reason": "Cancelar antes da execucao efetiva",
        },
    )

    assert create_response.status_code == 202
    job_id = create_response.json()["job_id"]

    cancel_response = client.post(
        f"/api/v1/helpdesk/automation/jobs/{job_id}/cancel",
        headers=AUTOMATION_APPROVAL_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "change_revoked",
        },
    )
    detail_response = client.get(
        f"/api/v1/helpdesk/automation/jobs/{job_id}",
        headers=AUTOMATION_READ_HEADERS,
    )

    assert cancel_response.status_code == 200
    body = cancel_response.json()
    assert body["approval_status"] == "approved"
    assert body["execution_status"] == "cancelled"
    assert body["cancelled_by"] == "supervisor-ana"
    assert body["cancellation_reason_code"] == "change_revoked"
    assert body["cancellation_reason"] == "Mudanca revogada antes da execucao."
    assert body["cancelled_at"] is not None
    assert body["queue_mode"] == "memory"

    assert detail_response.status_code == 200
    detail_body = detail_response.json()
    assert detail_body["execution_status"] == "cancelled"
    assert detail_body["cancelled_by"] == "supervisor-ana"
    assert detail_body["cancellation_reason_code"] == "change_revoked"

    events = [
        event
        for event in get_memory_audit_events()
        if event.event_type == "automation_job_cancelled"
    ]
    assert events
    assert events[-1].payload_json["job_id"] == job_id
    assert events[-1].payload_json["previous_execution_status"] == "queued"


def test_cancel_route_rejects_pending_manual_job() -> None:
    ticket_response = client.post(
        "/api/v1/helpdesk/tickets/open",
        json={
            "subject": "Chamado pendente para cancelamento invalido",
            "description": "Jobs aguardando aprovacao devem continuar usando reject.",
            "requester": {
                "external_id": "user-cancel-pending",
                "display_name": "Usuario Cancel Pending",
                "phone_number": "+5511913306677",
                "role": "user",
            },
        },
    )
    job_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "glpi.ticket_snapshot",
            "ticket_id": ticket_response.json()["ticket_id"],
            "reason": "Pendente de aprovacao normal",
        },
    )
    job_id = job_response.json()["job_id"]

    cancel_response = client.post(
        f"/api/v1/helpdesk/automation/jobs/{job_id}/cancel",
        headers=AUTOMATION_APPROVAL_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "change_revoked",
        },
    )

    assert cancel_response.status_code == 400
    assert "nao pode ser cancelado" in cancel_response.json()["detail"].lower()


def test_cancel_route_accepts_retry_scheduled_job() -> None:
    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "noop.healthcheck",
            "reason": "Preparar retry para cancelamento",
        },
    )
    job_id = create_response.json()["job_id"]

    store = OperationalStateStore(get_settings())
    acquired = asyncio.run(
        store.acquire_job_for_execution(
            job_id,
            worker_id="cancel-retry-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )
    assert acquired is not None
    retried = asyncio.run(
        store.mark_job_for_retry(
            job_id,
            worker_id="cancel-retry-worker",
            error_type="ValueError",
            error_message="Falha controlada antes do cancelamento.",
            retry_scheduled_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            retry_delay_seconds=60,
        )
    )
    assert retried is not None

    cancel_response = client.post(
        f"/api/v1/helpdesk/automation/jobs/{job_id}/cancel",
        headers=AUTOMATION_APPROVAL_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "scope_changed",
        },
    )

    assert cancel_response.status_code == 200
    body = cancel_response.json()
    assert body["execution_status"] == "cancelled"
    assert body["cancelled_by"] == "supervisor-ana"
    assert body["cancellation_reason_code"] == "scope_changed"
    assert body["cancellation_reason"] == "Escopo mudou antes da execucao."
    assert body["last_error"] == "Falha controlada antes do cancelamento."


def test_stale_pending_automation_job_is_expired_before_manual_approval() -> None:
    settings = get_settings()
    original_timeout = settings.automation_approval_timeout_minutes
    settings.automation_approval_timeout_minutes = 1

    try:
        ticket_response = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado com aprovacao vencida",
                "description": "Valida expiracao automatica de jobs aguardando revisao.",
                "requester": {
                    "external_id": "user-expired-approval",
                    "display_name": "Usuario Approval Expired",
                    "phone_number": "+5511912209900",
                    "role": "user",
                },
            },
        )
        ticket_id = ticket_response.json()["ticket_id"]

        create_response = client.post(
            "/api/v1/helpdesk/automation/jobs",
            headers=AUTOMATION_HEADERS,
            json={
                "requested_by": "ops-ana",
                "automation_name": "glpi.ticket_snapshot",
                "ticket_id": ticket_id,
                "reason": "Job aguardando revisao fora da janela",
            },
        )
        job_id = create_response.json()["job_id"]

        stale_timestamp = datetime.now(timezone.utc) - timedelta(minutes=5)
        operational_store_module._MEMORY_JOB_REQUESTS[job_id].created_at = stale_timestamp

        approve_response = client.post(
            f"/api/v1/helpdesk/automation/jobs/{job_id}/approve",
            headers=AUTOMATION_APPROVAL_HEADERS,
            json={
                "acted_by": "supervisor-ana",
                "reason_code": "change_window_validated",
            },
        )
        detail_response = client.get(
            f"/api/v1/helpdesk/automation/jobs/{job_id}",
            headers=AUTOMATION_READ_HEADERS,
        )
    finally:
        settings.automation_approval_timeout_minutes = original_timeout

    assert approve_response.status_code == 400
    assert "rejected/rejected" in approve_response.json()["detail"]

    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["approval_status"] == "rejected"
    assert body["execution_status"] == "rejected"
    assert body["approval_acted_by"] == "system-approval-expiration"
    assert body["approval_reason_code"] == "approval_timeout_expired"
    assert "expirada automaticamente" in body["approval_reason"].lower()

    events = [
        event
        for event in get_memory_audit_events()
        if event.event_type == "automation_job_approval_expired"
    ]
    assert len(events) == 1
    assert events[0].payload_json["job_id"] == job_id


def test_get_automation_job_exposes_retry_schedule_metadata() -> None:
    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "noop.healthcheck",
            "reason": "Preparar job para retry agendado",
        },
    )

    assert create_response.status_code == 202
    job_id = create_response.json()["job_id"]

    store = OperationalStateStore(get_settings())
    acquired = asyncio.run(
        store.acquire_job_for_execution(
            job_id,
            worker_id="retry-metadata-worker",
            queue_mode="memory",
            queue_key="helpdesk:automation:jobs",
        )
    )
    assert acquired is not None

    scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=45)
    retried = asyncio.run(
        store.mark_job_for_retry(
            job_id,
            worker_id="retry-metadata-worker",
            error_type="ValueError",
            error_message="Falha controlada para validar resposta.",
            retry_scheduled_at=scheduled_at,
            retry_delay_seconds=45,
        )
    )
    assert retried is not None

    detail_response = client.get(
        f"/api/v1/helpdesk/automation/jobs/{job_id}",
        headers=AUTOMATION_READ_HEADERS,
    )

    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["execution_status"] == "retry-scheduled"
    assert body["retry_scheduled_at"] == scheduled_at.isoformat()
    assert body["retry_delay_seconds"] == 45
    assert body["last_error"] == "Falha controlada para validar resposta."


def test_reject_manual_automation_job_marks_job_as_rejected() -> None:
    ticket_response = client.post(
        "/api/v1/helpdesk/tickets/open",
        json={
            "subject": "Chamado para rejeicao",
            "description": "Chamado de teste para validar rejeicao explicita da automacao.",
            "requester": {
                "external_id": "user-reject-probe",
                "display_name": "Usuario Reject Probe",
                "phone_number": "+5511912205566",
                "role": "user",
            },
        },
    )
    ticket_id = ticket_response.json()["ticket_id"]

    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "glpi.ticket_snapshot",
            "ticket_id": ticket_id,
            "reason": "Snapshot operacional pendente de revisao",
        },
    )
    job_id = create_response.json()["job_id"]

    reject_response = client.post(
        f"/api/v1/helpdesk/automation/jobs/{job_id}/reject",
        headers=AUTOMATION_APPROVAL_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "outside_change_window",
        },
    )

    assert reject_response.status_code == 200
    body = reject_response.json()
    assert body["job_id"] == job_id
    assert body["approval_status"] == "rejected"
    assert body["approval_acted_by"] == "supervisor-ana"
    assert body["approval_reason_code"] == "outside_change_window"
    assert body["approval_reason"] == "Ticket fora da janela de atendimento autorizada."
    assert body["execution_status"] == "rejected"
    assert body["queue_mode"] is None


def test_approve_route_rejects_auto_approved_job() -> None:
    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "noop.healthcheck",
            "reason": "Smoke test low-risk",
        },
    )
    job_id = create_response.json()["job_id"]

    approve_response = client.post(
        f"/api/v1/helpdesk/automation/jobs/{job_id}/approve",
        headers=AUTOMATION_APPROVAL_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "change_window_validated",
        },
    )

    assert approve_response.status_code == 400
    assert "nao esta aguardando aprovacao" in approve_response.json()["detail"].lower()


def test_approve_route_rejects_non_allowlisted_reason_code() -> None:
    ticket_response = client.post(
        "/api/v1/helpdesk/tickets/open",
        json={
            "subject": "Chamado para validar reason_code",
            "description": "Aprovacao deve bloquear reason_code fora da allowlist.",
            "requester": {
                "external_id": "user-invalid-reason-code",
                "display_name": "Usuario Invalid Reason Code",
                "phone_number": "+5511913317788",
                "role": "user",
            },
        },
    )
    create_response = client.post(
        "/api/v1/helpdesk/automation/jobs",
        headers=AUTOMATION_HEADERS,
        json={
            "requested_by": "ops-ana",
            "automation_name": "glpi.ticket_snapshot",
            "ticket_id": ticket_response.json()["ticket_id"],
            "reason": "Validar allowlist de motivos",
        },
    )

    approve_response = client.post(
        f"/api/v1/helpdesk/automation/jobs/{create_response.json()['job_id']}/approve",
        headers=AUTOMATION_APPROVAL_HEADERS,
        json={
            "acted_by": "supervisor-ana",
            "reason_code": "free_text_not_allowed",
        },
    )

    assert approve_response.status_code == 400
    assert "reason_code invalido" in approve_response.json()["detail"].lower()


def test_open_ticket_works_in_mock_mode() -> None:
    payload = {
        "subject": "Falha de acesso ao GLPI",
        "description": "Usuário relata que não consegue autenticar no portal do GLPI.",
        "category": "acesso",
        "asset_name": "glpi-web-01",
        "service_name": "glpi",
        "priority": "high",
        "requester": {
            "external_id": "user-123",
            "display_name": "Maria Santos",
            "phone_number": "+5521997775269",
            "role": "user",
        },
    }

    response = client.post("/api/v1/helpdesk/tickets/open", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["ticket_id"].startswith("GLPI-LOCAL-")
    assert body["integration_mode"] in {"mock", "mixed"}
    assert body["requester_glpi_user_id"] == 101
    assert body["identity_source"] == "directory"
    assert body["triage"]["resolved_category"] == "acesso"
    assert body["triage"]["suggested_queue"] == "ServiceDesk-Acessos"


def test_open_ticket_ignores_sensitive_requester_fields_from_client_payload() -> None:
    payload = {
        "subject": "Tentativa de injetar solicitante operacional",
        "description": "A rota protegida não deve confiar em papel ou glpi_user_id enviados pelo cliente.",
        "requester": {
            "external_id": "spoofed-user",
            "display_name": "Solicitante Forjado",
            "phone_number": "+5511900099999",
            "role": "admin",
            "team": "seguranca",
            "glpi_user_id": 999,
        },
    }

    response = client.post("/api/v1/helpdesk/tickets/open", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["requester_role"] == "user"
    assert body["requester_team"] is None
    assert body["requester_glpi_user_id"] is None
    assert body["identity_source"] == "direct"


def test_get_ticket_returns_local_mock_ticket() -> None:
    create_payload = {
        "subject": "Erro de impressão na matriz",
        "description": "Impressora da recepção parou de responder após troca de toner.",
        "category": "infra",
        "asset_name": "printer-matriz-01",
        "service_name": "impressao",
        "priority": "medium",
        "requester": {
            "external_id": "user-456",
            "display_name": "Carlos Lima",
            "phone_number": "+5511977776666",
            "role": "user",
        },
    }

    create_response = client.post("/api/v1/helpdesk/tickets/open", json=create_payload)
    ticket_id = create_response.json()["ticket_id"]

    response = client.get(f"/api/v1/helpdesk/tickets/{ticket_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == ticket_id
    assert body["subject"] == create_payload["subject"]
    assert body["status"] == "queued-local"
    assert body["integration_mode"] == "mock"


def test_audit_events_endpoint_lists_recent_events_without_sensitive_text() -> None:
    payload = {
        "subject": "Falha de acesso ao GLPI",
        "description": "Usuário relata que não consegue autenticar no portal do GLPI.",
        "category": "acesso",
        "asset_name": "glpi-web-01",
        "service_name": "glpi",
        "priority": "high",
        "requester": {
            "external_id": "user-audit-123",
            "display_name": "Maria Santos",
            "phone_number": "+5521997775269",
            "role": "user",
        },
    }

    create_response = client.post("/api/v1/helpdesk/tickets/open", json=payload)
    ticket_id = create_response.json()["ticket_id"]

    response = client.get(
        f"/api/v1/helpdesk/audit/events?event_type=ticket_opened&ticket_id={ticket_id}",
        headers=AUDIT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["storage_mode"] == "memory"
    assert body["retention_days"] == 30
    assert body["applied_filters"] == {
        "event_type": "ticket_opened",
        "ticket_id": ticket_id,
    }
    assert len(body["events"]) == 1
    event = body["events"][0]
    assert event["event_type"] == "ticket_opened"
    assert event["ticket_id"] == ticket_id
    assert event["source_channel"] == "api"
    assert event["payload_json"]["category"] == "acesso"
    assert event["payload_json"]["asset_name"] == "glpi-web-01"
    assert event["payload_json"]["service_name"] == "glpi"
    assert event["payload_json"]["glpi_external_id"].startswith("helpdesk-api-")
    assert event["payload_json"]["glpi_request_type_id"] == 4
    assert event["payload_json"]["glpi_request_type_name"] == "Direct"
    assert "subject" not in event["payload_json"]
    assert "description" not in event["payload_json"]
    assert any("fallback em memoria" in note.lower() for note in body["notes"])


def test_triage_endpoint_suggests_queue_and_next_steps() -> None:
    payload = {
        "subject": "Usuarios sem acesso ao ERP",
        "description": "Time financeiro relata erro de autenticacao e nenhum usuario consegue entrar no ERP.",
        "service_name": "erp",
    }

    response = client.post("/api/v1/helpdesk/triage", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_category"] == "acesso"
    assert body["resolved_priority"] == "high"
    assert body["suggested_queue"] == "ServiceDesk-Acessos"
    assert len(body["next_steps"]) >= 1


def test_triage_endpoint_uses_requester_context_to_route_operational_access_ticket() -> None:
    payload = {
        "subject": "Sem acesso VPN administrativo",
        "description": "Tecnico de infraestrutura perdeu acesso a VPN do bastion para atuar no ambiente.",
        "service_name": "vpn",
        "requester_role": "technician",
        "requester_team": "infraestrutura",
    }

    response = client.post("/api/v1/helpdesk/triage", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_category"] == "acesso"
    assert body["suggested_queue"] == "Infraestrutura-N1"
    assert any("contexto do solicitante" in note.lower() for note in body["notes"])


def test_triage_endpoint_aligns_llm_steps_with_final_operational_queue(monkeypatch) -> None:
    async def fake_try_llm_assist(
        self,
        payload,
        suggested_category,
        suggested_priority,
        suggested_queue,
        resolution_hints,
        similar_incidents,
    ):
        return (
            "Resumo ajustado por LLM.",
            [
                "Se necessário, redirecione o atendimento para o ServiceDesk-Acessos para análise adicional.",
            ],
            ["Triagem enriquecida pelo provider fake."],
        )

    monkeypatch.setattr(TriageAgent, "_try_llm_assist", fake_try_llm_assist)

    response = client.post(
        "/api/v1/helpdesk/triage",
        json={
            "subject": "Sem acesso VPN administrativo",
            "description": "Tecnico de infraestrutura perdeu acesso a VPN do bastion para atuar no ambiente.",
            "service_name": "vpn",
            "requester_role": "technician",
            "requester_team": "infraestrutura",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_queue"] == "Infraestrutura-N1"
    assert "ServiceDesk-Acessos" not in body["next_steps"][0]
    assert "Infraestrutura-N1" in body["next_steps"][0]


def test_open_ticket_applies_triage_when_priority_is_omitted() -> None:
    payload = {
        "subject": "Servico fora do ar",
        "description": "Aplicacao principal esta fora do ar e todos os usuarios ficaram indisponiveis.",
        "asset_name": "app-prod-01",
        "service_name": "erp",
        "requester": {
            "external_id": "user-crit-001",
            "display_name": "Operacao Financeira",
            "phone_number": "+5511900001111",
            "role": "user",
        },
    }

    response = client.post("/api/v1/helpdesk/tickets/open", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["triage"]["resolved_priority"] == "critical"
    assert body["routed_to"] == "NOC-Critico"
    assert "Resumo de triagem" in " ".join(body["notes"])


def test_open_ticket_routes_operational_identity_access_ticket_to_infra_queue() -> None:
    payload = {
        "subject": "Sem acesso VPN administrativo",
        "description": "Equipe de infraestrutura perdeu acesso a VPN do bastion para atender incidente.",
        "service_name": "vpn",
        "requester": {
            "external_id": "tech-ana-souza",
            "display_name": "Ana Souza",
            "phone_number": "+5511912345678",
            "role": "user",
        },
    }

    response = client.post("/api/v1/helpdesk/tickets/open", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["requester_role"] == "technician"
    assert body["requester_team"] == "infraestrutura"
    assert body["triage"]["suggested_queue"] == "Infraestrutura-N1"
    assert body["routed_to"] == "Infraestrutura-N1"


def test_open_ticket_generates_unique_mock_ids_even_when_time_source_repeats(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.services.glpi.time", lambda: 1710000000, raising=False)
    MOCK_TICKET_STORE.clear()

    payload = {
        "subject": "Teste de unicidade no modo mock",
        "description": "Abrindo dois chamados seguidos para validar IDs únicos.",
        "category": "infra",
        "asset_name": "app-node-01",
        "service_name": "api",
        "priority": "medium",
        "requester": {
            "external_id": "user-unique",
            "display_name": "Teste Unico",
            "phone_number": "+5511912340000",
            "role": "user",
        },
    }

    try:
        first_response = client.post("/api/v1/helpdesk/tickets/open", json=payload)
        second_response = client.post("/api/v1/helpdesk/tickets/open", json=payload)
    finally:
        MOCK_TICKET_STORE.clear()

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["ticket_id"] != second_response.json()["ticket_id"]


def test_get_unknown_ticket_returns_404() -> None:
    response = client.get("/api/v1/helpdesk/tickets/GLPI-LOCAL-inexistente")

    assert response.status_code == 404
    assert "não encontrado" in response.json()["detail"].lower()


def test_get_registered_identity_returns_directory_entry() -> None:
    response = client.get("/api/v1/helpdesk/identities/+5511912345678")

    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == "tech-ana-souza"
    assert body["role"] == "technician"
    assert body["team"] == "infraestrutura"
    assert body["glpi_user_id"] == 201


def test_get_registered_admin_identity_returns_directory_entry() -> None:
    response = client.get("/api/v1/helpdesk/identities/+5511900019999")

    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == "admin-ricardo-ops"
    assert body["role"] == "admin"
    assert body["team"] == "plataforma"


def test_whatsapp_message_uses_identity_directory_role() -> None:
    payload = {
        "sender_phone": "+5511912345678",
        "sender_name": "Nome ignorado",
        "text": "Quero consultar o incidente do servidor web.",
        "requester_role": "user",
        "service_name": "web",
        "priority": "medium",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "assistant"
    assert body["requester_role"] == "technician"
    assert body["requester_external_id"] == "tech-ana-souza"
    assert body["requester_team"] == "infraestrutura"
    assert body["requester_glpi_user_id"] == 201
    assert body["identity_source"] == "directory"
    assert body["assistant_result"]["flow_name"] == "technician_operations"
    assert "Não abri chamado automaticamente" in body["assistant_result"]["reply_text"]


def test_raw_whatsapp_messages_endpoint_requires_api_token() -> None:
    unauthenticated_client = TestClient(app)

    response = unauthenticated_client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5521997775269",
            "text": "Teste sem token",
        },
    )

    assert response.status_code == 401


def test_user_whatsapp_message_opens_ticket_by_default() -> None:
    payload = {
        "sender_phone": "+5521997775269",
        "sender_name": "Maria Santos",
        "text": "Estou sem acesso ao GLPI desde cedo.",
        "requester_role": "user",
        "service_name": "glpi",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "ticket"
    assert body["requester_role"] == "user"
    assert body["ticket"]["requester_glpi_user_id"] == 101


def test_user_ticket_description_uses_identity_resolved_by_phone_for_sender() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Nome incorreto do payload",
                "text": "Estou sem acesso ao GLPI desde cedo.",
                "requester_role": "user",
                "service_name": "glpi",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "ticket"

        ticket_id = body["ticket"]["ticket_id"]
        record = MOCK_TICKET_STORE[ticket_id]
        assert "Remetente: Maria Santos" in record.description
        assert "Nome incorreto do payload" not in record.description
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_greeting_starts_catalog_intake() -> None:
    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5521997775269",
            "sender_name": None,
            "text": "Oi",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "assistant"
    assert body["requester_role"] == "user"
    assert body["assistant_result"]["flow_name"] == "user_catalog_intake"
    assert body["assistant_result"]["intake_stage"] == "awaiting_catalog"
    assert any(option.startswith("1. ") for option in body["assistant_result"]["available_options"])
    assert "classificar" in body["assistant_result"]["reply_text"].lower()


def test_user_catalog_sequence_collects_context_before_opening_ticket() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": None,
                "text": "Oi",
                "requester_role": "user",
            },
        )
        second_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": None,
                "text": "1",
                "requester_role": "user",
            },
        )
        final_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": None,
                "text": "Nao consigo acessar o ERP desde 08:10.",
                "requester_role": "user",
            },
        )

        assert first_response.status_code == 202
        assert second_response.status_code == 202
        second_body = second_response.json()
        assert second_body["outcome_type"] == "assistant"
        assert second_body["assistant_result"]["intake_stage"] == "awaiting_description"

        assert final_response.status_code == 202
        final_body = final_response.json()
        assert final_body["outcome_type"] == "ticket"

        ticket_id = final_body["ticket"]["ticket_id"]
        record = MOCK_TICKET_STORE[ticket_id]
        assert record.subject.startswith("WhatsApp: Acesso / Login / Senha -")
        assert "Remetente: Maria Santos" in record.description
        assert "Tipo de chamado: Acesso / Login / Senha" in record.description
        assert "Historico da coleta:" in record.description
        assert "- Oi" in record.description
        assert "- 1" in record.description
        assert "- Nao consigo acessar o ERP desde 08:10." in record.description
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_intake_context_shift_reclassifies_new_issue() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Oi",
                "requester_role": "user",
            },
        )
        client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "1",
                "requester_role": "user",
            },
        )

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Na verdade a VPN nao conecta desde 07:40 e nao consigo acessar a rede do escritorio.",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "ticket"
        assert any("mudou o contexto" in note.lower() for note in body["notes"])

        ticket_id = body["ticket"]["ticket_id"]
        record = MOCK_TICKET_STORE[ticket_id]
        assert record.subject.startswith("WhatsApp: Rede / VPN / Internet -")
        assert "VPN" in record.description or "vpn" in record.description.lower()
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalize_ticket_lists_owned_options() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "ERP indisponível para o financeiro",
                "description": "Usuária precisa encerrar um chamado antigo depois da validação.",
                "category": "acesso",
                "service_name": "erp",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        second_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "GLPI sem acesso para aprovação",
                "description": "Usuária quer fechar o ticket mais recente após confirmação.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[first_ticket].status = "closed"
        MOCK_TICKET_STORE[second_ticket].status = "solved"

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "awaiting_ticket_selection"
        assert second_ticket in "\n".join(body["assistant_result"]["available_options"])
        assert first_ticket not in "\n".join(body["assistant_result"]["available_options"])
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalize_ticket_lists_open_owned_tickets_even_if_new_or_waiting() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado ainda novo para acompanhamento",
                "description": "Usuária quer saber por que ainda não consegue encerrar pelo WhatsApp.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        second_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado aguardando atendimento",
                "description": "Usuária ainda não pode fechar o ticket porque ele não avançou para resolução.",
                "category": "acesso",
                "service_name": "erp",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[first_ticket].status = "new"
        MOCK_TICKET_STORE[second_ticket].status = "waiting"

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "awaiting_ticket_selection"
        assert first_ticket in "\n".join(body["assistant_result"]["available_options"])
        assert second_ticket in "\n".join(body["assistant_result"]["available_options"])
        assert "status=new" in "\n".join(body["assistant_result"]["available_options"]).lower()
        assert "status=waiting" in "\n".join(body["assistant_result"]["available_options"]).lower()
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_intake_context_shift_to_finalization_switches_flow() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        existing_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado pronto para ser encerrado",
                "description": "Usuária precisa escolher um ticket para encerrar.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[existing_ticket].status = "solved"

        client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Oi",
                "requester_role": "user",
            },
        )
        client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "1",
                "requester_role": "user",
            },
        )

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Na verdade quero finalizar chamado",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "awaiting_ticket_selection"
        assert existing_ticket in "\n".join(body["assistant_result"]["available_options"])
        assert any("fluxo de finalização" in note.lower() for note in body["notes"])
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalize_ticket_selection_closes_chosen_ticket() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Primeiro chamado para manter aberto",
                "description": "Este chamado deve continuar aberto após a seleção.",
                "category": "acesso",
                "service_name": "erp",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        second_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Segundo chamado para finalizar",
                "description": "Este chamado deve ser finalizado quando a usuária escolher a primeira opção.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[first_ticket].status = "processing"
        MOCK_TICKET_STORE[second_ticket].status = "solved"

        prompt_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )
        assert prompt_response.status_code == 202

        final_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "1",
                "requester_role": "user",
            },
        )

        assert final_response.status_code == 202
        body = final_response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "completed"
        assert second_ticket in body["assistant_result"]["reply_text"]
        assert MOCK_TICKET_STORE[second_ticket].status == "closed"
        assert MOCK_TICKET_STORE[first_ticket].status == "processing"
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalize_ticket_selection_closes_new_ticket_when_user_resolved_it() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        first_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado novo resolvido pelo proprio usuario",
                "description": "Usuária resolveu o problema sem precisar aguardar atendimento técnico.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        second_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado em andamento para permanecer aberto",
                "description": "Este chamado não deve ser encerrado quando a usuária escolher a primeira opção.",
                "category": "acesso",
                "service_name": "erp",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[first_ticket].status = "new"
        MOCK_TICKET_STORE[second_ticket].status = "processing"

        prompt_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )
        assert prompt_response.status_code == 202

        final_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "2",
                "requester_role": "user",
            },
        )

        assert final_response.status_code == 202
        body = final_response.json()
        assert body["outcome_type"] == "assistant"
        assert body["assistant_result"]["flow_name"] == "user_ticket_finalization"
        assert body["assistant_result"]["intake_stage"] == "completed"
        assert first_ticket in body["assistant_result"]["reply_text"]
        assert MOCK_TICKET_STORE[first_ticket].status == "closed"
        assert MOCK_TICKET_STORE[second_ticket].status == "processing"
    finally:
        MOCK_TICKET_STORE.clear()


def test_user_finalization_context_shift_opens_new_ticket() -> None:
    MOCK_TICKET_STORE.clear()
    try:
        existing_ticket = client.post(
            "/api/v1/helpdesk/tickets/open",
            json={
                "subject": "Chamado antigo para finalização",
                "description": "Usuária iniciou o fluxo de finalização antes de mudar de assunto.",
                "category": "acesso",
                "service_name": "glpi",
                "requester": {
                    "external_id": "user-maria-santos",
                    "display_name": "Maria Santos",
                    "phone_number": "+5521997775269",
                    "role": "user",
                    "glpi_user_id": 101,
                },
            },
        ).json()["ticket_id"]
        MOCK_TICKET_STORE[existing_ticket].status = "solved"

        prompt_response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "finalizar chamado",
                "requester_role": "user",
            },
        )
        assert prompt_response.status_code == 202

        response = client.post(
            "/api/v1/webhooks/whatsapp/messages",
            json={
                "sender_phone": "+5521997775269",
                "sender_name": "Maria Santos",
                "text": "Na verdade estou sem VPN desde 07:40 e nao consigo conectar no escritorio.",
                "requester_role": "user",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["outcome_type"] == "ticket"
        assert any("saiu do contexto de finalização" in note.lower() for note in body["notes"])

        new_ticket_id = body["ticket"]["ticket_id"]
        assert new_ticket_id != existing_ticket
        assert MOCK_TICKET_STORE[existing_ticket].status == "solved"
        assert MOCK_TICKET_STORE[new_ticket_id].subject.startswith("WhatsApp: Rede / VPN / Internet -")
    finally:
        MOCK_TICKET_STORE.clear()


def test_technician_open_command_creates_ticket_explicitly() -> None:
    payload = {
        "sender_phone": "+5511912345678",
        "sender_name": "Ana Souza",
        "text": "/open ERP indisponível para o financeiro desde 08:00.",
        "requester_role": "user",
        "service_name": "erp",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "command"
    assert body["command_result"]["command_name"] == "open"
    assert body["command_result"]["opened_ticket"]["ticket_id"].startswith("GLPI-LOCAL-")
    assert body["command_result"]["opened_ticket"]["requester_glpi_user_id"] == 201


def test_admin_freeform_message_uses_admin_operational_flow() -> None:
    payload = {
        "sender_phone": "+5511900019999",
        "sender_name": "Ricardo Admin",
        "text": "Preciso revisar o impacto do incidente do ERP.",
        "requester_role": "user",
        "service_name": "erp",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "assistant"
    assert body["requester_role"] == "admin"
    assert body["assistant_result"]["flow_name"] == "admin_operations"


def test_technician_command_returns_operational_result() -> None:
    payload = {
        "sender_phone": "+5511912345678",
        "sender_name": "Ana Souza",
        "text": "/me",
        "requester_role": "user",
    }

    response = client.post("/api/v1/webhooks/whatsapp/messages", json=payload)

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "command"
    assert body["command_result"]["command_name"] == "me"
    assert body["command_result"]["status"] == "completed"
    assert body["requester_glpi_user_id"] == 201


def test_technician_ticket_command_reads_existing_ticket() -> None:
    create_payload = {
        "subject": "Servidor de banco sem resposta",
        "description": "Banco principal não responde às consultas do ERP.",
        "category": "infra",
        "asset_name": "db-prod-01",
        "service_name": "postgresql",
        "priority": "high",
        "requester": {
            "external_id": "user-789",
            "display_name": "Bruno Costa",
            "phone_number": "+5511966665555",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/ticket {ticket_id}",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["outcome_type"] == "command"
    assert body["command_result"]["command_name"] == "ticket"
    assert body["command_result"]["ticket"]["ticket_id"] == ticket_id
    assert body["command_result"]["resolution_advice"]["ticket_id"] == ticket_id
    assert "Sugestao:" in body["command_result"]["reply_text"]


def test_technician_comment_command_adds_followup() -> None:
    create_payload = {
        "subject": "ERP indisponível para o financeiro",
        "description": "Usuários do financeiro relatam falha ao autenticar.",
        "category": "acesso",
        "asset_name": "erp-fin-01",
        "service_name": "erp",
        "priority": "high",
        "requester": {
            "external_id": "user-100",
            "display_name": "Luciana Prado",
            "phone_number": "+5511955554444",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/comment {ticket_id} Coletando logs do host afetado.",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "comment"
    assert body["command_result"]["ticket"]["followup_count"] == 1
    assert body["command_result"]["resolution_advice"]["ticket_id"] == ticket_id
    assert body["command_result"]["resolution_advice"]["recent_entries"][0]["source"] == "followup"
    assert "Sugestao:" in body["command_result"]["reply_text"]


def test_technician_comment_command_notifies_requester() -> None:
    create_payload = {
        "subject": "ERP indisponível para o financeiro",
        "description": "Usuários do financeiro relatam falha ao autenticar.",
        "category": "acesso",
        "asset_name": "erp-fin-01",
        "service_name": "erp",
        "priority": "high",
        "requester": {
            "external_id": "user-carlos-lima",
            "display_name": "Carlos Lima",
            "phone_number": "+5511977776666",
            "role": "user",
            "glpi_user_id": 102,
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/comment {ticket_id} Reiniciei a validação do acesso e preciso que você teste novamente.",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "comment"
    assert "enviado ao solicitante" in body["command_result"]["reply_text"].lower()
    assert any("+5511977776666" in note for note in body["notes"])


def test_technician_status_command_updates_allowed_status() -> None:
    create_payload = {
        "subject": "VPN intermitente",
        "description": "Conexão cai a cada poucos minutos.",
        "category": "rede",
        "asset_name": "vpn-edge-01",
        "service_name": "vpn",
        "priority": "medium",
        "requester": {
            "external_id": "user-200",
            "display_name": "Renata Melo",
            "phone_number": "+5511944443333",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/status {ticket_id} processing",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "status"
    assert body["command_result"]["ticket"]["status"] == "processing"
    assert body["command_result"]["resolution_advice"]["ticket_id"] == ticket_id
    assert "Sugestao:" in body["command_result"]["reply_text"]


def test_technician_status_solved_records_solution_and_notifies_requester() -> None:
    create_payload = {
        "subject": "ERP indisponivel para o financeiro",
        "description": "Usuarios do financeiro relatam falha ao autenticar.",
        "category": "acesso",
        "asset_name": "erp-fin-01",
        "service_name": "erp",
        "priority": "high",
        "requester": {
            "external_id": "user-carlos-lima",
            "display_name": "Carlos Lima",
            "phone_number": "+5511977776666",
            "role": "user",
            "glpi_user_id": 102,
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/status {ticket_id} solved",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "status"
    assert body["command_result"]["ticket"]["status"] == "solved"
    assert body["command_result"]["resolution_advice"]["ticket_id"] == ticket_id
    assert body["command_result"]["resolution_advice"]["recent_entries"][0]["source"] == "solution"
    assert "atualizacao enviada ao solicitante" in body["command_result"]["reply_text"].lower()
    assert any("+5511977776666" in note for note in body["notes"])
    assert len(MOCK_TICKET_STORE[ticket_id].solutions) == 1
    assert "Resumo da resolucao:" in MOCK_TICKET_STORE[ticket_id].solutions[0]["content"]


def test_technician_status_command_denies_closed_status() -> None:
    create_payload = {
        "subject": "Chamado para validação",
        "description": "Validação de controle de permissão.",
        "category": "acesso",
        "asset_name": "auth-01",
        "service_name": "auth",
        "priority": "medium",
        "requester": {
            "external_id": "user-300",
            "display_name": "Rafael Nunes",
            "phone_number": "+5511933332222",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/status {ticket_id} closed",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["status"] == "forbidden"


def test_supervisor_assign_command_updates_assignee() -> None:
    create_payload = {
        "subject": "Fila de impressão parada",
        "description": "Impressoras da recepção não recebem novos jobs.",
        "category": "infra",
        "asset_name": "print-spool-01",
        "service_name": "print-spooler",
        "priority": "high",
        "requester": {
            "external_id": "user-400",
            "display_name": "Patricia Gomes",
            "phone_number": "+5511922221111",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5521972008679",
            "sender_name": "Paula Almeida",
            "text": f"/assign {ticket_id} tech-ana-souza",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["command_name"] == "assign"
    assert body["command_result"]["ticket"]["assigned_glpi_user_id"] == 201


def test_technician_assign_command_is_forbidden() -> None:
    create_payload = {
        "subject": "Supervisor only action",
        "description": "Teste de permissão de atribuição.",
        "category": "infra",
        "asset_name": "router-edge-02",
        "service_name": "routing",
        "priority": "medium",
        "requester": {
            "external_id": "user-500",
            "display_name": "Fabio Teixeira",
            "phone_number": "+5511910101010",
            "role": "user",
        },
    }
    ticket_id = client.post("/api/v1/helpdesk/tickets/open", json=create_payload).json()["ticket_id"]

    response = client.post(
        "/api/v1/webhooks/whatsapp/messages",
        json={
            "sender_phone": "+5511912345678",
            "sender_name": "Ana Souza",
            "text": f"/assign {ticket_id} supervisor-paula-almeida",
            "requester_role": "user",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["command_result"]["status"] == "forbidden"


def test_meta_webhook_creates_ticket_with_valid_signature() -> None:
    settings = get_settings()
    original_validate_signature = settings.whatsapp_validate_signature
    original_app_secret = settings.whatsapp_app_secret

    settings.whatsapp_validate_signature = True
    settings.whatsapp_app_secret = "super-secret"

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [
                                {
                                    "wa_id": "5511999999999",
                                    "profile": {"name": "João Silva"},
                                }
                            ],
                            "messages": [
                                {
                                    "from": "5511999999999",
                                    "id": "wamid.HBgLTESTE123",
                                    "timestamp": "1713456789",
                                    "type": "text",
                                    "text": {"body": "Estou sem acesso ao ERP"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(
        b"super-secret",
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    try:
        response = client.post(
            "/api/v1/webhooks/whatsapp/meta",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )
    finally:
        settings.whatsapp_validate_signature = original_validate_signature
        settings.whatsapp_app_secret = original_app_secret

    assert response.status_code == 202
    body = response.json()
    assert body["processed_messages"] == 1
    assert len(body["interactions"]) == 1
    assert body["interactions"][0]["ticket"]["ticket_id"].startswith("GLPI-LOCAL-")
    assert body["interactions"][0]["requester_external_id"] == "5511999999999"


def test_meta_webhook_rejects_invalid_signature() -> None:
    settings = get_settings()
    original_validate_signature = settings.whatsapp_validate_signature
    original_app_secret = settings.whatsapp_app_secret

    settings.whatsapp_validate_signature = True
    settings.whatsapp_app_secret = "super-secret"

    payload = {
        "object": "whatsapp_business_account",
        "entry": [],
    }

    try:
        response = client.post(
            "/api/v1/webhooks/whatsapp/meta",
            json=payload,
            headers={"X-Hub-Signature-256": "sha256=assinatura-invalida"},
        )
    finally:
        settings.whatsapp_validate_signature = original_validate_signature
        settings.whatsapp_app_secret = original_app_secret

    assert response.status_code == 403
    assert "assinatura" in response.json()["detail"].lower()
