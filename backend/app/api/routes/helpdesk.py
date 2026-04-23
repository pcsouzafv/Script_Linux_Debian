from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_agent_runtime_service,
    get_docker_runtime_client,
    get_helpdesk_orchestrator,
    get_llm_client,
    get_whatsapp_client,
)
from app.core.security import (
    require_api_access,
    require_audit_access,
    require_automation_access,
    require_automation_read_access,
    require_automation_approval_access,
)
from app.orchestration.helpdesk import HelpdeskOrchestrator
from app.schemas.helpdesk import (
    AgentInvestigationRequest,
    AgentInvestigationResponse,
    AutomationJobCreateRequest,
    AutomationJobDecisionRequest,
    AutomationJobListResponse,
    AutomationJobResponse,
    AutomationSummaryResponse,
    AuditEventListResponse,
    CorrelationRequest,
    CorrelationResponse,
    LLMGenerateRequest,
    LLMGenerateResponse,
    IdentityLookupResponse,
    LLMStatusResponse,
    NormalizedWhatsAppMessage,
    RuntimeAuditOverviewResponse,
    RuntimeAutomationRunnerStatusResponse,
    RuntimeDockerApplicationResponse,
    RuntimeDockerContainerResponse,
    RuntimeDockerOverviewResponse,
    RuntimeHealthResponse,
    RuntimeMessagingStatusResponse,
    RuntimeOperationalStoreStatusResponse,
    RuntimeOverviewResponse,
    RuntimeQueueStatusResponse,
    RuntimeServiceStatusResponse,
    RuntimeSessionListResponse,
    RuntimeSessionResponse,
    TicketResolutionAdviceResponse,
    TicketOperationsSummaryResponse,
    TicketTriageRequest,
    TicketTriageResponse,
    TicketDetailsResponse,
    TicketOpenRequest,
    TicketOpenResponse,
    WhatsAppInteractionResponse,
    WhatsAppWebhookProcessingResponse,
)
from app.agent_runtime import AgentRuntimeService
from app.services.ansible_runner import ansible_runner_module
from app.services.docker_runtime import DockerRuntimeClient
from app.services.llm import LLMClient
from app.services.whatsapp import WhatsAppClient

router = APIRouter(tags=["helpdesk"])
protected_dependencies = [Depends(require_api_access)]
automation_write_dependencies = [Depends(require_automation_access)]
automation_read_dependencies = [Depends(require_automation_read_access)]
automation_approval_dependencies = [Depends(require_automation_approval_access)]


@router.post(
    "/helpdesk/triage",
    response_model=TicketTriageResponse,
    dependencies=protected_dependencies,
)
async def triage_ticket(
    payload: TicketTriageRequest,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> TicketTriageResponse:
    return await orchestrator.triage_ticket(payload)


@router.post(
    "/helpdesk/tickets/open",
    response_model=TicketOpenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=protected_dependencies,
)
async def open_ticket(
    payload: TicketOpenRequest,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> TicketOpenResponse:
    return await orchestrator.open_ticket(payload)


@router.get(
    "/helpdesk/tickets/{ticket_id}",
    response_model=TicketDetailsResponse,
    dependencies=protected_dependencies,
)
async def get_ticket(
    ticket_id: str,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> TicketDetailsResponse:
    return await orchestrator.get_ticket(ticket_id)


@router.get(
    "/helpdesk/audit/events",
    response_model=AuditEventListResponse,
    dependencies=[Depends(require_audit_access)],
)
async def get_audit_events(
    limit: int = Query(default=20, ge=1, le=100),
    event_type: str | None = Query(default=None, min_length=3, max_length=80),
    ticket_id: str | None = Query(default=None, min_length=2, max_length=120),
    actor_external_id: str | None = Query(default=None, min_length=2, max_length=120),
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AuditEventListResponse:
    return await orchestrator.list_audit_events(
        limit=limit,
        event_type=event_type,
        ticket_id=ticket_id,
        actor_external_id=actor_external_id,
    )


@router.get(
    "/helpdesk/reports/tickets/summary",
    response_model=TicketOperationsSummaryResponse,
    dependencies=[Depends(require_audit_access)],
)
async def get_ticket_operations_summary(
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> TicketOperationsSummaryResponse:
    return await orchestrator.get_ticket_operations_summary()


@router.get(
    "/helpdesk/runtime/overview",
    response_model=RuntimeOverviewResponse,
    dependencies=[Depends(require_audit_access), Depends(require_automation_read_access)],
)
async def get_runtime_overview(
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
    docker_runtime_client: DockerRuntimeClient = Depends(get_docker_runtime_client),
    llm_client: LLMClient = Depends(get_llm_client),
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
    settings: Settings = Depends(get_settings),
) -> RuntimeOverviewResponse:
    audit_result = await orchestrator.list_audit_events(limit=25)
    session_result = await orchestrator.operational_store.list_sessions(limit=12)
    queue_snapshot = await orchestrator.job_queue.get_queue_snapshot()
    docker_snapshot = await docker_runtime_client.get_runtime_snapshot(limit=10)
    ticket_summary = await orchestrator.get_ticket_operations_summary()
    automation_summary = await orchestrator.get_automation_summary()
    llm_status = llm_client.get_status()

    event_type_counts = Counter(event.event_type for event in audit_result.events)
    source_channel_counts = Counter(event.source_channel for event in audit_result.events)
    status_counts = Counter(event.status for event in audit_result.events)

    return RuntimeOverviewResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        health=RuntimeHealthResponse(
            status="ok",
            service=settings.app_name,
            environment=settings.environment,
            api_prefix=settings.api_prefix,
            host=settings.api_host,
            port=settings.api_port,
        ),
        identity_provider=settings.identity_provider,
        glpi=_build_glpi_runtime_status(orchestrator, settings),
        zabbix=_build_zabbix_runtime_status(orchestrator, settings),
        llm=LLMStatusResponse(
            enabled=llm_status.enabled,
            provider=llm_status.provider,
            model=llm_status.model,
            status=llm_status.status,
            base_url=llm_status.base_url,
            notes=llm_status.notes,
        ),
        messaging=_build_messaging_runtime_status(whatsapp_client, settings),
        operational_store=_build_operational_store_runtime_status(
            settings,
            session_storage_mode=session_result.storage_mode,
            audit_storage_mode=audit_result.storage_mode,
            extra_notes=[*session_result.notes, *audit_result.notes],
        ),
        queue=_build_queue_runtime_status(settings, queue_snapshot),
        automation_runner=_build_automation_runner_runtime_status(orchestrator, settings),
        docker=_build_docker_runtime_overview(docker_snapshot),
        sessions=RuntimeSessionListResponse(
            storage_mode=session_result.storage_mode,
            total_sessions=session_result.total_sessions,
            sessions=[
                RuntimeSessionResponse(
                    phone_number_masked=_mask_phone_number(session.phone_number),
                    requester_display_name=session.requester_display_name,
                    flow_name=session.flow_name,
                    stage=session.stage,
                    selected_catalog_code=session.selected_catalog_code,
                    transcript_entries=len(session.transcript),
                    ticket_options_count=len(session.ticket_options),
                    updated_at=session.updated_at.isoformat(),
                )
                for session in session_result.sessions
            ],
            notes=session_result.notes,
        ),
        audit=RuntimeAuditOverviewResponse(
            storage_mode=audit_result.storage_mode,
            retention_days=audit_result.retention_days,
            recent_event_count=len(audit_result.events),
            event_type_counts=dict(event_type_counts),
            source_channel_counts=dict(source_channel_counts),
            status_counts=dict(status_counts),
            recent_events=audit_result.events,
            notes=audit_result.notes,
        ),
        ticket_operations=ticket_summary,
        automation=automation_summary,
    )


@router.post(
    "/helpdesk/agent/investigate",
    response_model=AgentInvestigationResponse,
    dependencies=automation_read_dependencies,
)
async def investigate_with_agent_runtime(
    payload: AgentInvestigationRequest,
    agent_runtime_service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentInvestigationResponse:
    return await agent_runtime_service.investigate(payload)


@router.post(
    "/helpdesk/automation/jobs",
    response_model=AutomationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=automation_write_dependencies,
)
async def create_automation_job(
    payload: AutomationJobCreateRequest,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AutomationJobResponse:
    try:
        return await orchestrator.request_automation_job(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/helpdesk/automation/jobs",
    response_model=AutomationJobListResponse,
    dependencies=automation_read_dependencies,
)
async def list_automation_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    automation_name: str | None = Query(default=None, min_length=3, max_length=80),
    ticket_id: str | None = Query(default=None, min_length=2, max_length=120),
    approval_status: str | None = Query(default=None, min_length=4, max_length=40),
    execution_status: str | None = Query(default=None, min_length=4, max_length=40),
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AutomationJobListResponse:
    return await orchestrator.list_automation_jobs(
        limit=limit,
        automation_name=automation_name,
        ticket_id=ticket_id,
        approval_status=approval_status,
        execution_status=execution_status,
    )


@router.get(
    "/helpdesk/automation/summary",
    response_model=AutomationSummaryResponse,
    dependencies=automation_read_dependencies,
)
async def get_automation_summary(
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AutomationSummaryResponse:
    return await orchestrator.get_automation_summary()


@router.get(
    "/helpdesk/automation/jobs/{job_id}",
    response_model=AutomationJobResponse,
    dependencies=automation_read_dependencies,
)
async def get_automation_job(
    job_id: str,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AutomationJobResponse:
    return await orchestrator.get_automation_job(job_id)


@router.post(
    "/helpdesk/automation/jobs/{job_id}/approve",
    response_model=AutomationJobResponse,
    dependencies=automation_approval_dependencies,
)
async def approve_automation_job(
    job_id: str,
    payload: AutomationJobDecisionRequest,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AutomationJobResponse:
    try:
        return await orchestrator.approve_automation_job(job_id, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/helpdesk/automation/jobs/{job_id}/reject",
    response_model=AutomationJobResponse,
    dependencies=automation_approval_dependencies,
)
async def reject_automation_job(
    job_id: str,
    payload: AutomationJobDecisionRequest,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AutomationJobResponse:
    try:
        return await orchestrator.reject_automation_job(job_id, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/helpdesk/automation/jobs/{job_id}/cancel",
    response_model=AutomationJobResponse,
    dependencies=automation_approval_dependencies,
)
async def cancel_automation_job(
    job_id: str,
    payload: AutomationJobDecisionRequest,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> AutomationJobResponse:
    try:
        return await orchestrator.cancel_automation_job(job_id, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/helpdesk/identities/{phone_number}",
    response_model=IdentityLookupResponse,
    dependencies=protected_dependencies,
)
async def get_identity(
    phone_number: str,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> IdentityLookupResponse:
    return await orchestrator.get_registered_identity(phone_number)


@router.post(
    "/helpdesk/incidents/correlate",
    response_model=CorrelationResponse,
    dependencies=protected_dependencies,
)
async def correlate_incident(
    payload: CorrelationRequest,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> CorrelationResponse:
    return await orchestrator.correlate(payload)


@router.get(
    "/helpdesk/ai/status",
    response_model=LLMStatusResponse,
    dependencies=protected_dependencies,
)
async def get_llm_status(
    llm_client: LLMClient = Depends(get_llm_client),
) -> LLMStatusResponse:
    status = llm_client.get_status()
    return LLMStatusResponse(
        enabled=status.enabled,
        provider=status.provider,
        model=status.model,
        status=status.status,
        base_url=status.base_url,
        notes=status.notes,
    )


@router.post(
    "/helpdesk/ai/generate",
    response_model=LLMGenerateResponse,
    dependencies=protected_dependencies,
)
async def generate_with_llm(
    payload: LLMGenerateRequest,
    llm_client: LLMClient = Depends(get_llm_client),
) -> LLMGenerateResponse:
    result = await llm_client.generate_text(
        user_prompt=payload.prompt,
        system_prompt=payload.system_prompt,
        max_tokens=payload.max_tokens,
        temperature=payload.temperature,
    )
    return LLMGenerateResponse(
        provider=result.provider,
        model=result.model,
        status=result.status,
        content=result.content,
        notes=result.notes,
    )


@router.get(
    "/helpdesk/ai/tickets/{ticket_id}/resolution",
    response_model=TicketResolutionAdviceResponse,
    dependencies=protected_dependencies,
)
async def advise_ticket_resolution(
    ticket_id: str,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> TicketResolutionAdviceResponse:
    return await orchestrator.advise_ticket_resolution(ticket_id)


@router.get("/webhooks/whatsapp/verify", include_in_schema=False)
async def verify_whatsapp_webhook(
    mode: str = Query(..., alias="hub.mode"),
    challenge: str = Query(..., alias="hub.challenge"),
    verify_token: str = Query(..., alias="hub.verify_token"),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    if not settings.whatsapp_verify_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token de verificação do webhook do WhatsApp não configurado.",
        )

    if mode != "subscribe" or verify_token != settings.whatsapp_verify_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Falha na verificação do webhook do WhatsApp.",
        )
    return PlainTextResponse(challenge)


@router.post(
    "/webhooks/whatsapp/meta",
    response_model=WhatsAppWebhookProcessingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_meta_whatsapp_webhook(
    request: Request,
    signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    settings: Settings = Depends(get_settings),
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> WhatsAppWebhookProcessingResponse:
    if settings.whatsapp_validate_signature and not settings.whatsapp_app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook da Meta não está completamente configurado.",
        )

    raw_body = await request.body()
    if not whatsapp_client.validate_webhook_signature(raw_body, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Falha na validação da assinatura do webhook do WhatsApp.",
        )

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload JSON inválido no webhook do WhatsApp.",
        ) from exc

    messages, ignored_events = whatsapp_client.normalize_webhook_payload(payload)
    return await orchestrator.process_whatsapp_webhook_messages(messages, ignored_events)


@router.post(
    "/webhooks/whatsapp/evolution",
    response_model=WhatsAppWebhookProcessingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_evolution_whatsapp_webhook(
    request: Request,
    secret_header: str | None = Header(default=None, alias="X-Evolution-Webhook-Secret"),
    secret_query: str | None = Query(default=None, alias="secret"),
    settings: Settings = Depends(get_settings),
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> WhatsAppWebhookProcessingResponse:
    if not settings.evolution_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook da Evolution API não está configurado com segredo dedicado.",
        )

    raw_body = await request.body()
    provided_secret = secret_header or secret_query
    if not whatsapp_client.validate_evolution_webhook_secret(provided_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Falha na validação do segredo do webhook da Evolution API.",
        )

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload JSON inválido no webhook da Evolution API.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload JSON inválido no webhook da Evolution API.",
        )

    messages, ignored_events = whatsapp_client.normalize_evolution_webhook_payload(payload)
    return await orchestrator.process_whatsapp_webhook_messages(messages, ignored_events)


@router.post(
    "/webhooks/whatsapp/messages",
    response_model=WhatsAppInteractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=protected_dependencies,
    include_in_schema=False,
)
async def receive_whatsapp_message(
    payload: NormalizedWhatsAppMessage,
    orchestrator: HelpdeskOrchestrator = Depends(get_helpdesk_orchestrator),
) -> WhatsAppInteractionResponse:
    return await orchestrator.process_whatsapp_message(payload)


def _build_glpi_runtime_status(
    orchestrator: HelpdeskOrchestrator,
    settings: Settings,
) -> RuntimeServiceStatusResponse:
    configured = orchestrator.glpi_client.configured
    notes = [
        "Integracao GLPI operando em modo configurado." if configured else "Integracao GLPI operando em modo mock local."
    ]
    return RuntimeServiceStatusResponse(
        configured=configured,
        status="configured" if configured else "mock",
        mode="live" if configured else "mock",
        base_url=settings.glpi_base_url,
        notes=notes,
    )


def _build_zabbix_runtime_status(
    orchestrator: HelpdeskOrchestrator,
    settings: Settings,
) -> RuntimeServiceStatusResponse:
    configured = orchestrator.zabbix_client.configured
    notes = [
        "Correlacao Zabbix operando com backend real." if configured else "Correlacao Zabbix operando em modo mock local."
    ]
    return RuntimeServiceStatusResponse(
        configured=configured,
        status="configured" if configured else "mock",
        mode="live" if configured else "mock",
        base_url=settings.zabbix_base_url,
        notes=notes,
    )


def _build_messaging_runtime_status(
    whatsapp_client: WhatsAppClient,
    settings: Settings,
) -> RuntimeMessagingStatusResponse:
    resolved_provider = _resolve_whatsapp_delivery_provider(whatsapp_client, settings)
    notes: list[str] = []
    if resolved_provider == "mock":
        notes.append("Mensageria esta respondendo em modo mock local.")
    elif resolved_provider == "meta":
        notes.append("Mensageria esta roteando respostas pelo WhatsApp Cloud API da Meta.")
    else:
        notes.append("Mensageria esta roteando respostas pela Evolution API.")

    if settings.whatsapp_validate_signature:
        notes.append("Validacao de assinatura do webhook Meta esta habilitada.")
    else:
        notes.append("Validacao de assinatura do webhook Meta esta desabilitada para desenvolvimento.")

    return RuntimeMessagingStatusResponse(
        delivery_provider=settings.whatsapp_delivery_provider,
        resolved_delivery_provider=resolved_provider,
        configured=whatsapp_client.configured,
        meta_configured=whatsapp_client.meta_configured,
        evolution_configured=whatsapp_client.evolution_configured,
        public_number=settings.whatsapp_public_number,
        webhook_verify_token_configured=bool(settings.whatsapp_verify_token),
        signature_validation_enabled=settings.whatsapp_validate_signature,
        evolution_webhook_secret_configured=bool(settings.evolution_webhook_secret),
        notes=notes,
    )


def _build_operational_store_runtime_status(
    settings: Settings,
    *,
    session_storage_mode: str,
    audit_storage_mode: str,
    extra_notes: list[str],
) -> RuntimeOperationalStoreStatusResponse:
    postgres_configured = bool(settings.operational_postgres_dsn)
    storage_modes = {session_storage_mode, audit_storage_mode}

    if not postgres_configured:
        status = "memory"
        mode = "memory"
        notes = [
            "PostgreSQL operacional nao configurado; store segue em modo local de desenvolvimento.",
        ]
    elif storage_modes == {"postgres"}:
        status = "configured"
        mode = "postgres"
        notes = [
            "Store operacional persistindo sessao e auditoria em PostgreSQL.",
        ]
    else:
        status = "fallback"
        mode = "mixed" if "postgres" in storage_modes else "memory"
        notes = [
            "Store operacional degradado para fallback local em memoria em parte do fluxo.",
        ]

    return RuntimeOperationalStoreStatusResponse(
        configured=postgres_configured,
        status=status,
        mode=mode,
        schema_name=settings.operational_postgres_schema,
        session_storage_mode=session_storage_mode,
        audit_storage_mode=audit_storage_mode,
        audit_retention_days=settings.operational_audit_retention_days,
        job_retention_days=settings.operational_job_retention_days,
        notes=[*notes, *extra_notes],
    )


def _build_queue_runtime_status(
    settings: Settings,
    queue_snapshot,
) -> RuntimeQueueStatusResponse:
    redis_configured = bool(settings.redis_url)
    if not redis_configured:
        status = "memory"
        notes = [
            "Redis nao configurado; fila operacional segue apenas em memoria.",
            *queue_snapshot.notes,
        ]
    elif queue_snapshot.queue_mode == "redis":
        status = "configured"
        notes = [
            "Fila operacional conectada ao Redis configurado.",
            *queue_snapshot.notes,
        ]
    else:
        status = "fallback"
        notes = [
            "Redis configurado, mas a fila operacional esta em fallback local em memoria.",
            *queue_snapshot.notes,
        ]

    return RuntimeQueueStatusResponse(
        configured=redis_configured,
        status=status,
        mode=queue_snapshot.queue_mode,
        queue_key=queue_snapshot.queue_key,
        dead_letter_queue_key=queue_snapshot.dead_letter_queue_key,
        queue_depth=queue_snapshot.queue_depth,
        dead_letter_queue_depth=queue_snapshot.dead_letter_queue_depth,
        notes=notes,
    )


def _build_automation_runner_runtime_status(
    orchestrator: HelpdeskOrchestrator,
    settings: Settings,
) -> RuntimeAutomationRunnerStatusResponse:
    runner_client = orchestrator.automation_service.ansible_runner_client
    base_dir = Path(settings.automation_runner_base_dir)
    if not base_dir.is_absolute():
        base_dir = (runner_client.backend_dir / base_dir).resolve()
    else:
        base_dir = base_dir.resolve()

    available_projects = sorted(
        child.name
        for child in base_dir.iterdir()
        if child.is_dir()
    ) if base_dir.exists() else []
    catalog_entries = orchestrator.automation_service.get_catalog()
    runner_catalog_entries = [
        entry
        for entry in catalog_entries
        if entry.executor == "ansible-runner"
    ]

    if ansible_runner_module is None:
        configured = False
        status = "incomplete"
        notes = ["ansible-runner nao esta instalado no ambiente atual do backend."]
    elif not base_dir.exists():
        configured = False
        status = "incomplete"
        notes = [
            "Diretorio base do runner homologado nao foi encontrado no host atual.",
        ]
    else:
        configured = True
        status = "configured"
        notes = [
            "Runner homologado pronto para executar playbooks permitidos do catalogo.",
        ]

    if available_projects:
        notes.append(
            f"Projetos homologados detectados: {', '.join(available_projects)}."
        )

    return RuntimeAutomationRunnerStatusResponse(
        configured=configured,
        status=status,
        mode="ansible-runner",
        base_dir=str(base_dir),
        project_count=len(available_projects),
        available_projects=available_projects,
        catalog_entry_count=len(runner_catalog_entries),
        notes=notes,
    )


def _build_docker_runtime_overview(snapshot) -> RuntimeDockerOverviewResponse:
    return RuntimeDockerOverviewResponse(
        configured=snapshot.configured,
        status=snapshot.status,
        mode=snapshot.mode,
        binary_path=snapshot.binary_path,
        application_count=snapshot.application_count,
        total_containers=snapshot.total_containers,
        running_count=snapshot.running_count,
        exited_count=snapshot.exited_count,
        restarting_count=snapshot.restarting_count,
        unhealthy_count=snapshot.unhealthy_count,
        applications=[
            RuntimeDockerApplicationResponse(
                application_name=application.application_name,
                status=application.status,
                total_containers=application.total_containers,
                running_count=application.running_count,
                unhealthy_count=application.unhealthy_count,
                application_services=application.application_services,
                support_services=application.support_services,
                notes=application.notes,
            )
            for application in snapshot.applications
        ],
        containers=[
            RuntimeDockerContainerResponse(
                container_id=container.container_id,
                name=container.name,
                image=container.image,
                status=container.status,
                state=container.state,
                application_name=container.application_name,
                service_role=container.service_role,
                health_status=container.health_status,
                compose_project=container.compose_project,
                compose_service=container.compose_service,
                ports=container.ports,
            )
            for container in snapshot.containers
        ],
        notes=snapshot.notes,
    )


def _resolve_whatsapp_delivery_provider(
    whatsapp_client: WhatsAppClient,
    settings: Settings,
) -> str:
    provider = settings.whatsapp_delivery_provider.strip().lower()
    if provider in {"meta", "evolution", "mock"}:
        return provider
    if whatsapp_client.meta_configured:
        return "meta"
    if whatsapp_client.evolution_configured:
        return "evolution"
    return "mock"


def _mask_phone_number(phone_number: str) -> str:
    normalized = "".join(character for character in str(phone_number) if character.isdigit())
    if not normalized:
        return "session-without-phone"
    if len(normalized) <= 4:
        return normalized
    return f"***{normalized[-4:]}"
