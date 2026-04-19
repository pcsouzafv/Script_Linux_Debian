from enum import StrEnum

from pydantic import BaseModel, Field


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


class TicketTriageRequest(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10)
    current_category: str | None = Field(default=None, max_length=100)
    current_priority: TicketPriority | None = None
    asset_name: str | None = Field(default=None, max_length=120)
    service_name: str | None = Field(default=None, max_length=120)


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
    mode: str
    notes: list[str] = Field(default_factory=list)


class TicketOpenResponse(BaseModel):
    ticket_id: str
    status: str
    routed_to: str
    integration_mode: str
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


class TechnicianCommandResponse(BaseModel):
    command_name: str
    status: str
    operation_mode: str
    reply_text: str
    ticket: TicketDetailsResponse | None = None
    opened_ticket: TicketOpenResponse | None = None
    correlation: CorrelationResponse | None = None
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
