import re
from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


OPERATOR_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{1,119}$")


def _validate_operator_identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not OPERATOR_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must use 2-120 ASCII chars without spaces; allowed: letters, digits, dot, underscore, at, colon and hyphen"
        )
    return normalized


class UserRole(StrEnum):
    USER = "user"
    TECHNICIAN = "technician"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RequesterIdentity(BaseModel):
    external_id: str = Field(..., min_length=2, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    phone_number: str | None = Field(default=None, max_length=30)
    role: UserRole = UserRole.USER
    team: str | None = Field(default=None, max_length=120)
    glpi_user_id: int | None = Field(default=None, ge=1)


class TicketOpenRequest(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10)
    requester: RequesterIdentity
    category: str | None = Field(default=None, max_length=100)
    asset_name: str | None = Field(default=None, max_length=120)
    service_name: str | None = Field(default=None, max_length=120)
    priority: TicketPriority = TicketPriority.MEDIUM


class NormalizedWhatsAppMessage(BaseModel):
    sender_phone: str = Field(..., min_length=8, max_length=30)
    sender_name: str | None = Field(default=None, max_length=120)
    text: str = Field(..., min_length=1, max_length=2000)
    external_message_id: str | None = Field(default=None, max_length=120)
    requester_role: UserRole = UserRole.USER
    asset_name: str | None = Field(default=None, max_length=120)
    service_name: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=100)
    priority: TicketPriority = TicketPriority.MEDIUM


class CorrelatedEvent(BaseModel):
    source: str
    event_id: str
    severity: str
    summary: str
    host: str | None = None


class CorrelationRequest(BaseModel):
    asset_name: str | None = Field(default=None, max_length=120)
    service_name: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=5, ge=1, le=20)


class CorrelationResponse(BaseModel):
    mode: str
    events: list[CorrelatedEvent] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AgentInvestigationRequest(BaseModel):
    ticket_id: str | None = Field(default=None, min_length=1, max_length=120)
    asset_name: str | None = Field(default=None, max_length=120)
    service_name: str | None = Field(default=None, max_length=120)
    thread_id: str | None = Field(default=None, min_length=2, max_length=120)
    requested_by: str | None = Field(default="agent-shadow", min_length=2, max_length=120)

    @field_validator("ticket_id", "asset_name", "service_name", "thread_id", mode="before")
    @classmethod
    def normalize_optional_fields(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("requested_by")
    @classmethod
    def validate_requested_by(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_operator_identifier(value, "requested_by")

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_operator_identifier(value, "thread_id")

    @model_validator(mode="after")
    def validate_scope(self) -> "AgentInvestigationRequest":
        if not (self.ticket_id or self.asset_name or self.service_name):
            raise ValueError(
                "Informe pelo menos ticket_id, asset_name ou service_name para a investigacao."
            )
        return self


class AgentInvestigationPolicyResponse(BaseModel):
    mode: str
    can_read_data: bool
    can_execute_write_actions: bool
    approval_required_for_write: bool
    rationale: str
    notes: list[str] = Field(default_factory=list)


class AgentInvestigationEvidenceResponse(BaseModel):
    source: str
    kind: str
    title: str
    detail: str | None = None
    severity: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentInvestigationKnowledgeHitResponse(BaseModel):
    kind: str
    source: str
    title: str
    snippet: str
    reference: str
    score: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentInvestigationMemoryHitResponse(BaseModel):
    namespace: str
    memory_key: str
    title: str
    summary: str
    hypothesis: str | None = None
    category_name: str | None = None
    service_name: str | None = None
    asset_name: str | None = None
    source_ticket_id: str | None = None
    recommended_actions: list[str] = Field(default_factory=list)
    references_json: list[dict[str, Any]] = Field(default_factory=list)
    usage_count: int = 0
    score: int = 0
    updated_at: str


class AgentInvestigationResponse(BaseModel):
    mode: str
    thread_id: str
    checkpoint_mode: str
    checkpoint_history_count: int = 0
    ticket_id: str | None = None
    subject: str | None = None
    ticket_status: str | None = None
    priority: str | None = None
    category_name: str | None = None
    asset_name: str | None = None
    service_name: str | None = None
    routed_to: str | None = None
    source_channel: str | None = None
    summary: str
    hypothesis: str | None = None
    recommended_actions: list[str] = Field(default_factory=list)
    candidate_automations: list[str] = Field(default_factory=list)
    correlated_events: list[CorrelatedEvent] = Field(default_factory=list)
    knowledge_hits: list[AgentInvestigationKnowledgeHitResponse] = Field(default_factory=list)
    memory_hits: list[AgentInvestigationMemoryHitResponse] = Field(default_factory=list)
    used_tools: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    policy: AgentInvestigationPolicyResponse
    evidence: list[AgentInvestigationEvidenceResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LLMStatusResponse(BaseModel):
    enabled: bool
    provider: str
    model: str | None = None
    status: str
    base_url: str | None = None
    notes: list[str] = Field(default_factory=list)


class LLMGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    system_prompt: str | None = Field(default=None, max_length=4000)
    max_tokens: int = Field(default=400, ge=1, le=4000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class LLMGenerateResponse(BaseModel):
    provider: str
    model: str
    status: str
    content: str
    notes: list[str] = Field(default_factory=list)


class TicketResolutionEntryResponse(BaseModel):
    source: str
    content: str
    created_at: str | None = None
    author_glpi_user_id: int | None = None


class TicketResolutionAdviceResponse(BaseModel):
    ticket_id: str
    subject: str
    status: str
    priority: str | None = None
    category_name: str | None = None
    service_name: str | None = None
    routed_to: str | None = None
    integration_mode: str
    summary: str
    suggested_actions: list[str] = Field(default_factory=list)
    resolution_hints: list[str] = Field(default_factory=list)
    similar_incidents: list[str] = Field(default_factory=list)
    recent_entries: list[TicketResolutionEntryResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TicketTriageRequest(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10)
    current_category: str | None = Field(default=None, max_length=100)
    current_priority: TicketPriority | None = None
    asset_name: str | None = Field(default=None, max_length=120)
    service_name: str | None = Field(default=None, max_length=120)
    requester_role: UserRole | None = None
    requester_team: str | None = Field(default=None, max_length=120)


class TicketTriageResponse(BaseModel):
    current_category: str | None = None
    current_priority: TicketPriority | None = None
    suggested_category: str | None = None
    suggested_priority: TicketPriority
    resolved_category: str | None = None
    resolved_priority: TicketPriority
    suggested_queue: str
    confidence: str
    summary: str
    next_steps: list[str] = Field(default_factory=list)
    resolution_hints: list[str] = Field(default_factory=list)
    similar_incidents: list[str] = Field(default_factory=list)
    mode: str
    notes: list[str] = Field(default_factory=list)


class TicketOpenResponse(BaseModel):
    ticket_id: str
    status: str
    routed_to: str
    integration_mode: str
    glpi_assignment_group_id: int | None = None
    glpi_assignment_group_name: str | None = None
    requester_role: UserRole
    requester_external_id: str | None = None
    requester_display_name: str | None = None
    requester_team: str | None = None
    requester_glpi_user_id: int | None = None
    identity_source: str | None = None
    triage: TicketTriageResponse | None = None
    correlation: list[CorrelatedEvent] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TicketDetailsResponse(BaseModel):
    ticket_id: str
    subject: str
    status: str
    priority: str | None = None
    updated_at: str | None = None
    requester_glpi_user_id: int | None = None
    assigned_glpi_user_id: int | None = None
    followup_count: int = 0
    integration_mode: str
    notes: list[str] = Field(default_factory=list)


class WhatsAppWebhookProcessingResponse(BaseModel):
    processed_messages: int
    interactions: list["WhatsAppInteractionResponse"] = Field(default_factory=list)
    ignored_events: list[str] = Field(default_factory=list)
    integration_mode: str = "noop"


class IdentityLookupResponse(BaseModel):
    phone_number: str
    external_id: str
    display_name: str | None = None
    role: UserRole
    team: str | None = None
    glpi_user_id: int | None = None
    source: str
    notes: list[str] = Field(default_factory=list)


class AuditEventResponse(BaseModel):
    event_id: str
    created_at: str
    event_type: str
    actor_external_id: str | None = None
    actor_role: str | None = None
    ticket_id: str | None = None
    source_channel: str
    status: str
    payload_json: dict[str, Any] = Field(default_factory=dict)


class AuditEventListResponse(BaseModel):
    storage_mode: str
    retention_days: int | None = None
    applied_filters: dict[str, str] = Field(default_factory=dict)
    events: list[AuditEventResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AutomationJobCreateRequest(BaseModel):
    requested_by: str = Field(..., min_length=2, max_length=120)
    automation_name: str = Field(..., min_length=3, max_length=80)
    ticket_id: str | None = Field(default=None, min_length=2, max_length=120)
    reason: str | None = Field(default=None, max_length=240)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("requested_by")
    @classmethod
    def validate_requested_by(cls, value: str) -> str:
        return _validate_operator_identifier(value, "requested_by")


class AutomationJobDecisionRequest(BaseModel):
    acted_by: str = Field(..., min_length=2, max_length=120)
    reason_code: str = Field(
        ...,
        min_length=3,
        max_length=80,
        validation_alias=AliasChoices("reason_code", "reason"),
    )

    @field_validator("acted_by")
    @classmethod
    def validate_acted_by(cls, value: str) -> str:
        return _validate_operator_identifier(value, "acted_by")


class AutomationJobResponse(BaseModel):
    job_id: str
    created_at: str
    requested_by: str | None = None
    ticket_id: str | None = None
    automation_name: str
    risk_level: str | None = None
    approval_mode: str | None = None
    approval_required: bool = False
    approval_status: str
    approval_acted_by: str | None = None
    approval_reason_code: str | None = None
    approval_reason: str | None = None
    approval_updated_at: str | None = None
    execution_status: str
    attempt_count: int = 0
    max_attempts: int = 1
    retry_scheduled_at: str | None = None
    retry_delay_seconds: int | None = None
    last_error: str | None = None
    dead_lettered_at: str | None = None
    cancelled_by: str | None = None
    cancellation_reason_code: str | None = None
    cancellation_reason: str | None = None
    cancelled_at: str | None = None
    queue_mode: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class AutomationJobListResponse(BaseModel):
    storage_mode: str
    applied_filters: dict[str, str] = Field(default_factory=dict)
    jobs: list[AutomationJobResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AutomationSummaryResponse(BaseModel):
    storage_mode: str
    queue_mode: str
    approval_timeout_minutes: int | None = None
    total_jobs: int = 0
    approval_status_counts: dict[str, int] = Field(default_factory=dict)
    execution_status_counts: dict[str, int] = Field(default_factory=dict)
    queue_depth: int = 0
    dead_letter_queue_depth: int = 0
    oldest_job_created_at: str | None = None
    oldest_pending_approval_started_at: str | None = None
    oldest_pending_approval_expires_at: str | None = None
    oldest_queued_job_created_at: str | None = None
    oldest_running_started_at: str | None = None
    oldest_retry_scheduled_at: str | None = None
    notes: list[str] = Field(default_factory=list)


class MassIncidentCandidateResponse(BaseModel):
    scope: str
    correlation_key: str
    label: str
    category_name: str | None = None
    routed_to: str | None = None
    ticket_count: int = 0
    high_priority_ticket_count: int = 0
    unassigned_ticket_count: int = 0
    oldest_ticket_updated_at: str | None = None
    newest_ticket_updated_at: str | None = None
    ticket_ids: list[str] = Field(default_factory=list)
    sample_subjects: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TicketOperationsSummaryResponse(BaseModel):
    storage_mode: str
    total_tickets: int = 0
    unresolved_backlog_count: int = 0
    assigned_backlog_count: int = 0
    unassigned_backlog_count: int = 0
    high_priority_backlog_count: int = 0
    resolved_ticket_count: int = 0
    closed_ticket_count: int = 0
    backlog_assignment_coverage_percent: float = 0.0
    resolution_rate_percent: float = 0.0
    average_correlation_event_count: float = 0.0
    status_counts: dict[str, int] = Field(default_factory=dict)
    priority_counts: dict[str, int] = Field(default_factory=dict)
    source_channel_counts: dict[str, int] = Field(default_factory=dict)
    category_counts: dict[str, int] = Field(default_factory=dict)
    routed_to_counts: dict[str, int] = Field(default_factory=dict)
    mass_incident_candidate_count: int = 0
    mass_incident_candidates: list[MassIncidentCandidateResponse] = Field(default_factory=list)
    oldest_backlog_updated_at: str | None = None
    newest_snapshot_updated_at: str | None = None
    notes: list[str] = Field(default_factory=list)


class RuntimeHealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    api_prefix: str
    host: str
    port: int


class RuntimeServiceStatusResponse(BaseModel):
    configured: bool
    status: str
    mode: str
    base_url: str | None = None
    notes: list[str] = Field(default_factory=list)


class RuntimeMessagingStatusResponse(BaseModel):
    delivery_provider: str
    resolved_delivery_provider: str
    configured: bool
    meta_configured: bool = False
    evolution_configured: bool = False
    public_number: str | None = None
    webhook_verify_token_configured: bool = False
    signature_validation_enabled: bool = False
    evolution_webhook_secret_configured: bool = False
    notes: list[str] = Field(default_factory=list)


class RuntimeSessionResponse(BaseModel):
    phone_number_masked: str
    requester_display_name: str | None = None
    flow_name: str
    stage: str
    selected_catalog_code: str | None = None
    transcript_entries: int = 0
    ticket_options_count: int = 0
    updated_at: str


class RuntimeSessionListResponse(BaseModel):
    storage_mode: str
    total_sessions: int = 0
    sessions: list[RuntimeSessionResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RuntimeAuditOverviewResponse(BaseModel):
    storage_mode: str
    retention_days: int | None = None
    recent_event_count: int = 0
    event_type_counts: dict[str, int] = Field(default_factory=dict)
    source_channel_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    recent_events: list[AuditEventResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RuntimeOperationalStoreStatusResponse(BaseModel):
    configured: bool
    status: str
    mode: str
    schema_name: str
    session_storage_mode: str
    audit_storage_mode: str
    audit_retention_days: int | None = None
    job_retention_days: int | None = None
    notes: list[str] = Field(default_factory=list)


class RuntimeQueueStatusResponse(BaseModel):
    configured: bool
    status: str
    mode: str
    queue_key: str
    dead_letter_queue_key: str
    queue_depth: int = 0
    dead_letter_queue_depth: int = 0
    notes: list[str] = Field(default_factory=list)


class RuntimeAutomationRunnerStatusResponse(BaseModel):
    configured: bool
    status: str
    mode: str
    base_dir: str
    project_count: int = 0
    available_projects: list[str] = Field(default_factory=list)
    catalog_entry_count: int = 0
    notes: list[str] = Field(default_factory=list)


class RuntimeDockerContainerResponse(BaseModel):
    container_id: str
    name: str
    image: str
    status: str
    state: str
    application_name: str | None = None
    service_role: str
    health_status: str | None = None
    compose_project: str | None = None
    compose_service: str | None = None
    ports: str | None = None


class RuntimeDockerApplicationResponse(BaseModel):
    application_name: str
    status: str
    total_containers: int = 0
    running_count: int = 0
    unhealthy_count: int = 0
    application_services: list[str] = Field(default_factory=list)
    support_services: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RuntimeDockerOverviewResponse(BaseModel):
    configured: bool
    status: str
    mode: str
    binary_path: str | None = None
    application_count: int = 0
    total_containers: int = 0
    running_count: int = 0
    exited_count: int = 0
    restarting_count: int = 0
    unhealthy_count: int = 0
    applications: list[RuntimeDockerApplicationResponse] = Field(default_factory=list)
    containers: list[RuntimeDockerContainerResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RuntimeOverviewResponse(BaseModel):
    generated_at: str
    health: RuntimeHealthResponse
    identity_provider: str
    glpi: RuntimeServiceStatusResponse
    zabbix: RuntimeServiceStatusResponse
    llm: LLMStatusResponse
    messaging: RuntimeMessagingStatusResponse
    operational_store: RuntimeOperationalStoreStatusResponse
    queue: RuntimeQueueStatusResponse
    automation_runner: RuntimeAutomationRunnerStatusResponse
    docker: RuntimeDockerOverviewResponse
    sessions: RuntimeSessionListResponse
    audit: RuntimeAuditOverviewResponse
    ticket_operations: TicketOperationsSummaryResponse
    automation: AutomationSummaryResponse


class TechnicianCommandResponse(BaseModel):
    command_name: str
    status: str
    operation_mode: str
    reply_text: str
    ticket: TicketDetailsResponse | None = None
    opened_ticket: TicketOpenResponse | None = None
    correlation: CorrelationResponse | None = None
    resolution_advice: TicketResolutionAdviceResponse | None = None
    notes: list[str] = Field(default_factory=list)


class OperationalAssistantResponse(BaseModel):
    role: UserRole
    flow_name: str
    reply_text: str
    triage: TicketTriageResponse | None = None
    available_commands: list[str] = Field(default_factory=list)
    available_options: list[str] = Field(default_factory=list)
    intake_stage: str | None = None
    selected_option: str | None = None
    notes: list[str] = Field(default_factory=list)


class WhatsAppInteractionResponse(BaseModel):
    outcome_type: str
    integration_mode: str
    requester_role: UserRole
    requester_external_id: str | None = None
    requester_display_name: str | None = None
    requester_team: str | None = None
    requester_glpi_user_id: int | None = None
    identity_source: str | None = None
    ticket: TicketOpenResponse | None = None
    command_result: TechnicianCommandResponse | None = None
    assistant_result: OperationalAssistantResponse | None = None
    notes: list[str] = Field(default_factory=list)


WhatsAppWebhookProcessingResponse.model_rebuild()
