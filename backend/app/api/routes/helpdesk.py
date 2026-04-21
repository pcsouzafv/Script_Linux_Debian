import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.core.config import Settings, get_settings
from app.core.dependencies import (
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
    TicketTriageRequest,
    TicketTriageResponse,
    TicketDetailsResponse,
    TicketOpenRequest,
    TicketOpenResponse,
    WhatsAppInteractionResponse,
    WhatsAppWebhookProcessingResponse,
)
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
    secret: str | None = Header(default=None, alias="X-Evolution-Webhook-Secret"),
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
    if not whatsapp_client.validate_evolution_webhook_secret(secret):
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
