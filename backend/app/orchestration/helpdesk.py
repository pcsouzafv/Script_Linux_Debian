from datetime import datetime, timedelta, timezone

from app.schemas.helpdesk import (
    AutomationJobCreateRequest,
    AutomationJobDecisionRequest,
    AutomationJobListResponse,
    AutomationJobResponse,
    AutomationSummaryResponse,
    AuditEventListResponse,
    AuditEventResponse,
    CorrelationRequest,
    CorrelationResponse,
    IdentityLookupResponse,
    NormalizedWhatsAppMessage,
    OperationalAssistantResponse,
    RequesterIdentity,
    TechnicianCommandResponse,
    TicketDetailsResponse,
    TicketOpenRequest,
    TicketOpenResponse,
    TicketPriority,
    TicketResolutionAdviceResponse,
    TicketResolutionEntryResponse,
    TicketTriageRequest,
    TicketTriageResponse,
    UserRole,
    WhatsAppInteractionResponse,
    WhatsAppWebhookProcessingResponse,
)
from app.services.automation import AutomationService
from app.services.exceptions import IntegrationError, ResourceNotFoundError
from app.services.glpi import GLPIClient
from app.services.identity import IdentityService
from app.services.intake import UserIntakeOutcome, UserIntakeService, UserTicketOption
from app.services.job_queue import JobQueueService
from app.services.llm import LLMClient
from app.services.operational_store import JobRequestRecord, OperationalStateStore
from app.services.ticket_analytics_store import TicketAnalyticsStore
from app.services.triage import TriageAgent, resolve_helpdesk_queue
from app.services.whatsapp import WhatsAppClient
from app.services.zabbix import ZabbixClient


ALLOWED_OPERATOR_COMMANDS: dict[UserRole, set[str]] = {
    UserRole.TECHNICIAN: {"help", "me", "open", "ticket", "correlate", "comment", "status"},
    UserRole.SUPERVISOR: {
        "help",
        "me",
        "open",
        "ticket",
        "correlate",
        "comment",
        "status",
        "assign",
    },
    UserRole.ADMIN: {
        "help",
        "me",
        "open",
        "ticket",
        "correlate",
        "comment",
        "status",
        "assign",
    },
}

OPERATIONAL_ROLES = {
    UserRole.TECHNICIAN,
    UserRole.SUPERVISOR,
    UserRole.ADMIN,
}

TECHNICIAN_ALLOWED_STATUSES = {"processing", "planned", "waiting", "solved"}
PRIVILEGED_ALLOWED_STATUSES = {"new", "processing", "planned", "waiting", "solved", "closed"}
USER_FINALIZABLE_STATUSES = {"new", "processing", "planned", "waiting", "solved"}
AUTO_APPROVAL_REASON_CODE = "policy_auto_approved"
AUTO_APPROVAL_REASON = "Automacao classificada como low-risk no catalogo homologado."
APPROVAL_REASON_LABELS = {
    "change_window_validated": "Janela operacional validada.",
    "read_only_diagnostic_authorized": "Diagnostico read-only autorizado.",
    "risk_review_completed": "Revisao de risco concluida.",
    "rollback_plan_confirmed": "Plano de rollback confirmado.",
}
REJECTION_REASON_LABELS = {
    "outside_change_window": "Ticket fora da janela de atendimento autorizada.",
    "risk_not_authorized": "Risco acima da alçada autorizada.",
    "missing_prerequisites": "Pre-requisitos operacionais nao atendidos.",
    "insufficient_evidence": "Evidencias insuficientes para aprovar a execucao.",
}
CANCELLATION_REASON_LABELS = {
    "change_revoked": "Mudanca revogada antes da execucao.",
    "scope_changed": "Escopo mudou antes da execucao.",
    "duplicate_request": "Solicitacao duplicada cancelada.",
    "manual_intervention_completed": "Intervencao manual concluida; execucao nao e mais necessaria.",
}


class HelpdeskOrchestrator:
    def __init__(
        self,
        glpi_client: GLPIClient,
        zabbix_client: ZabbixClient,
        whatsapp_client: WhatsAppClient,
        llm_client: LLMClient,
        identity_service: IdentityService,
        automation_service: AutomationService,
        triage_agent: TriageAgent,
        user_intake_service: UserIntakeService,
        operational_store: OperationalStateStore,
        analytics_store: TicketAnalyticsStore,
        job_queue: JobQueueService,
    ) -> None:
        self.glpi_client = glpi_client
        self.zabbix_client = zabbix_client
        self.whatsapp_client = whatsapp_client
        self.llm_client = llm_client
        self.identity_service = identity_service
        self.automation_service = automation_service
        self.triage_agent = triage_agent
        self.user_intake_service = user_intake_service
        self.operational_store = operational_store
        self.analytics_store = analytics_store
        self.job_queue = job_queue

    async def triage_ticket(self, payload: TicketTriageRequest) -> TicketTriageResponse:
        return await self.triage_agent.triage(payload)

    async def open_ticket(self, request: TicketOpenRequest) -> TicketOpenResponse:
        resolved_requester = await self.identity_service.resolve_protected_api_requester(
            request.requester
        )
        validated_request = request.model_copy(
            update={
                "requester": resolved_requester.requester,
            }
        )
        triage = await self.triage_ticket(
            TicketTriageRequest(
                subject=validated_request.subject,
                description=validated_request.description,
                current_category=validated_request.category,
                current_priority=(
                    validated_request.priority
                    if "priority" in request.model_fields_set
                    else None
                ),
                asset_name=validated_request.asset_name,
                service_name=validated_request.service_name,
            )
        )
        effective_request = validated_request.model_copy(
            update={
                "category": triage.resolved_category,
                "priority": triage.resolved_priority,
            }
        )
        correlation, correlation_mode, correlation_notes = await self._safe_correlate(
            asset_name=effective_request.asset_name,
            service_name=effective_request.service_name,
            limit=5,
        )
        ticket_result = await self.glpi_client.create_ticket(effective_request)

        notes = [
            *resolved_requester.notes,
            *triage.notes,
            *correlation_notes,
            *ticket_result.notes,
            f"Resumo de triagem: {triage.summary}",
            *(f"Próximo passo sugerido: {step}" for step in triage.next_steps),
            *(f"Sugestao de resolucao: {hint}" for hint in triage.resolution_hints),
            *(f"Caso similar recente: {incident}" for incident in triage.similar_incidents),
            f"Fila sugerida: {triage.suggested_queue}.",
        ]

        integration_mode = self._merge_modes(ticket_result.mode, correlation_mode)
        await self._audit_event(
            event_type="ticket_opened",
            actor_external_id=effective_request.requester.external_id,
            actor_role=effective_request.requester.role.value,
            ticket_id=ticket_result.ticket_id,
            source_channel=self._resolve_source_channel(effective_request.subject),
            status=ticket_result.status,
            payload_json={
                "category": effective_request.category,
                "asset_name": effective_request.asset_name,
                "service_name": effective_request.service_name,
                "priority": effective_request.priority.value,
                "routed_to": triage.suggested_queue,
                "identity_source": resolved_requester.source,
                "integration_mode": integration_mode,
                "correlation_event_count": len(correlation),
                "glpi_external_id": ticket_result.external_id,
                "glpi_request_type_id": ticket_result.request_type_id,
                "glpi_request_type_name": ticket_result.request_type_name,
                "glpi_category_id": ticket_result.category_id,
                "glpi_category_name": ticket_result.category_name,
                "glpi_linked_item_type": ticket_result.linked_item_type,
                "glpi_linked_item_id": ticket_result.linked_item_id,
                "glpi_linked_item_name": ticket_result.linked_item_name,
            },
        )

        return TicketOpenResponse(
            ticket_id=ticket_result.ticket_id,
            status=ticket_result.status,
            routed_to=triage.suggested_queue,
            integration_mode=integration_mode,
            requester_role=effective_request.requester.role,
            requester_external_id=effective_request.requester.external_id,
            requester_display_name=effective_request.requester.display_name,
            requester_team=effective_request.requester.team,
            requester_glpi_user_id=effective_request.requester.glpi_user_id,
            identity_source=resolved_requester.source,
            triage=triage,
            correlation=correlation,
            notes=notes,
        )

    async def process_whatsapp_message(
        self,
        message: NormalizedWhatsAppMessage,
    ) -> WhatsAppInteractionResponse:
        resolved_identity = await self.identity_service.resolve_requester(
            phone_number=message.sender_phone,
            fallback_name=message.sender_name,
            fallback_role=message.requester_role,
        )
        if self._should_handle_as_operator_command(
            message.text,
            resolved_identity.requester.role,
        ):
            command_result = await self._run_operator_command(
                message=message,
                requester=resolved_identity.requester,
            )
            delivery_result = await self.whatsapp_client.send_text_message(
                to_number=message.sender_phone,
                body=command_result.reply_text,
            )
            notes = [*resolved_identity.notes, *command_result.notes, *delivery_result.notes]
            return WhatsAppInteractionResponse(
                outcome_type="command",
                integration_mode=self._merge_modes(
                    command_result.operation_mode,
                    delivery_result.mode,
                ),
                requester_role=resolved_identity.requester.role,
                requester_external_id=resolved_identity.requester.external_id,
                requester_display_name=resolved_identity.requester.display_name,
                requester_team=resolved_identity.requester.team,
                requester_glpi_user_id=resolved_identity.requester.glpi_user_id,
                identity_source=resolved_identity.source,
                command_result=command_result,
                notes=notes,
            )

        if resolved_identity.requester.role in OPERATIONAL_ROLES:
            assistant_result = await self._run_operator_assistant(
                message=message,
                requester=resolved_identity.requester,
            )
            delivery_result = await self.whatsapp_client.send_text_message(
                to_number=message.sender_phone,
                body=assistant_result.reply_text,
            )
            notes = [*resolved_identity.notes, *assistant_result.notes, *delivery_result.notes]
            return WhatsAppInteractionResponse(
                outcome_type="assistant",
                integration_mode=self._merge_modes("local", delivery_result.mode),
                requester_role=resolved_identity.requester.role,
                requester_external_id=resolved_identity.requester.external_id,
                requester_display_name=resolved_identity.requester.display_name,
                requester_team=resolved_identity.requester.team,
                requester_glpi_user_id=resolved_identity.requester.glpi_user_id,
                identity_source=resolved_identity.source,
                assistant_result=assistant_result,
                notes=notes,
            )

        context_notes: list[str] = []
        if await self.user_intake_service.has_active_session(message.sender_phone):
            context_decision = await self.user_intake_service.interpret_active_session(
                message=message,
                requester=resolved_identity.requester,
            )
            if context_decision.action == "switch_to_ticket_finalization":
                await self.user_intake_service.clear_session(
                    message.sender_phone,
                    reason="context_switch_to_ticket_finalization",
                )
                finalization_prompt = await self._start_user_ticket_finalization(
                    message=message,
                    requester=resolved_identity.requester,
                )
                assistant_notes = [*context_decision.notes, *finalization_prompt.notes]
                delivery_result = await self.whatsapp_client.send_text_message(
                    to_number=message.sender_phone,
                    body=finalization_prompt.reply_text,
                )
                assistant_result = OperationalAssistantResponse(
                    role=resolved_identity.requester.role,
                    flow_name=finalization_prompt.flow_name,
                    reply_text=finalization_prompt.reply_text,
                    available_options=finalization_prompt.available_options,
                    intake_stage=finalization_prompt.intake_stage,
                    selected_option=finalization_prompt.selected_option,
                    notes=assistant_notes,
                )
                notes = [*resolved_identity.notes, *assistant_result.notes, *delivery_result.notes]
                return WhatsAppInteractionResponse(
                    outcome_type="assistant",
                    integration_mode=self._merge_modes("local", delivery_result.mode),
                    requester_role=resolved_identity.requester.role,
                    requester_external_id=resolved_identity.requester.external_id,
                    requester_display_name=resolved_identity.requester.display_name,
                    requester_team=resolved_identity.requester.team,
                    requester_glpi_user_id=resolved_identity.requester.glpi_user_id,
                    identity_source=resolved_identity.source,
                    assistant_result=assistant_result,
                    notes=notes,
                )
            if context_decision.action == "switch_to_new_ticket_intake":
                await self.user_intake_service.clear_session(
                    message.sender_phone,
                    reason="context_switch_to_new_ticket_intake",
                )
                context_notes = context_decision.notes

        if await self.user_intake_service.has_pending_ticket_finalization(message.sender_phone):
            finalization_selection = await self.user_intake_service.handle_ticket_finalization_selection(
                phone_number=message.sender_phone,
                text=message.text,
            )
            if finalization_selection.action == "assistant":
                delivery_result = await self.whatsapp_client.send_text_message(
                    to_number=message.sender_phone,
                    body=finalization_selection.reply_text,
                )
                assistant_result = OperationalAssistantResponse(
                    role=resolved_identity.requester.role,
                    flow_name=finalization_selection.flow_name,
                    reply_text=finalization_selection.reply_text,
                    available_options=finalization_selection.available_options,
                    intake_stage=finalization_selection.intake_stage,
                    selected_option=finalization_selection.selected_option,
                    notes=[*context_notes, *finalization_selection.notes],
                )
                notes = [*resolved_identity.notes, *assistant_result.notes, *delivery_result.notes]
                return WhatsAppInteractionResponse(
                    outcome_type="assistant",
                    integration_mode=self._merge_modes("local", delivery_result.mode),
                    requester_role=resolved_identity.requester.role,
                    requester_external_id=resolved_identity.requester.external_id,
                    requester_display_name=resolved_identity.requester.display_name,
                    requester_team=resolved_identity.requester.team,
                    requester_glpi_user_id=resolved_identity.requester.glpi_user_id,
                    identity_source=resolved_identity.source,
                    assistant_result=assistant_result,
                    notes=notes,
                )

            finalized_assistant = await self._finalize_selected_user_ticket(
                requester=resolved_identity.requester,
                selected_ticket_id=finalization_selection.selected_ticket_id or "",
                selected_option=finalization_selection.selected_option,
                base_notes=[*context_notes, *finalization_selection.notes],
            )
            delivery_result = await self.whatsapp_client.send_text_message(
                to_number=message.sender_phone,
                body=finalized_assistant.reply_text,
            )
            notes = [*resolved_identity.notes, *finalized_assistant.notes, *delivery_result.notes]
            return WhatsAppInteractionResponse(
                outcome_type="assistant",
                integration_mode=self._merge_modes("local", delivery_result.mode),
                requester_role=resolved_identity.requester.role,
                requester_external_id=resolved_identity.requester.external_id,
                requester_display_name=resolved_identity.requester.display_name,
                requester_team=resolved_identity.requester.team,
                requester_glpi_user_id=resolved_identity.requester.glpi_user_id,
                identity_source=resolved_identity.source,
                assistant_result=finalized_assistant,
                notes=notes,
            )

        if self.user_intake_service.matches_ticket_finalization_intent(message.text):
            finalization_prompt = await self._start_user_ticket_finalization(
                message=message,
                requester=resolved_identity.requester,
            )
            delivery_result = await self.whatsapp_client.send_text_message(
                to_number=message.sender_phone,
                body=finalization_prompt.reply_text,
            )
            assistant_result = OperationalAssistantResponse(
                role=resolved_identity.requester.role,
                flow_name=finalization_prompt.flow_name,
                reply_text=finalization_prompt.reply_text,
                available_options=finalization_prompt.available_options,
                intake_stage=finalization_prompt.intake_stage,
                selected_option=finalization_prompt.selected_option,
                notes=[*context_notes, *finalization_prompt.notes],
            )
            notes = [*resolved_identity.notes, *assistant_result.notes, *delivery_result.notes]
            return WhatsAppInteractionResponse(
                outcome_type="assistant",
                integration_mode=self._merge_modes("local", delivery_result.mode),
                requester_role=resolved_identity.requester.role,
                requester_external_id=resolved_identity.requester.external_id,
                requester_display_name=resolved_identity.requester.display_name,
                requester_team=resolved_identity.requester.team,
                requester_glpi_user_id=resolved_identity.requester.glpi_user_id,
                identity_source=resolved_identity.source,
                assistant_result=assistant_result,
                notes=notes,
            )

        intake_outcome = await self.user_intake_service.handle_message(
            message=message,
            requester=resolved_identity.requester,
        )
        if intake_outcome.action == "assistant":
            delivery_result = await self.whatsapp_client.send_text_message(
                to_number=message.sender_phone,
                body=intake_outcome.reply_text,
            )
            assistant_result = OperationalAssistantResponse(
                role=resolved_identity.requester.role,
                flow_name=intake_outcome.flow_name,
                reply_text=intake_outcome.reply_text,
                available_options=intake_outcome.available_options,
                intake_stage=intake_outcome.intake_stage,
                selected_option=intake_outcome.selected_option,
                notes=[*context_notes, *intake_outcome.notes],
            )
            notes = [*resolved_identity.notes, *assistant_result.notes, *delivery_result.notes]
            return WhatsAppInteractionResponse(
                outcome_type="assistant",
                integration_mode=self._merge_modes("local", delivery_result.mode),
                requester_role=resolved_identity.requester.role,
                requester_external_id=resolved_identity.requester.external_id,
                requester_display_name=resolved_identity.requester.display_name,
                requester_team=resolved_identity.requester.team,
                requester_glpi_user_id=resolved_identity.requester.glpi_user_id,
                identity_source=resolved_identity.source,
                assistant_result=assistant_result,
                notes=notes,
            )

        request = self._build_ticket_request_from_message(
            message,
            resolved_identity.requester,
            intake_outcome=intake_outcome,
        )
        response = await self.open_ticket(request)
        delivery_result = await self.whatsapp_client.send_text_message(
            to_number=message.sender_phone,
            body=self._build_confirmation_message(response),
        )

        ticket_response = response.model_copy(
            update={
                "notes": [*response.notes, *context_notes, *resolved_identity.notes, *delivery_result.notes],
                "requester_role": resolved_identity.requester.role,
                "requester_external_id": resolved_identity.requester.external_id,
                "requester_display_name": resolved_identity.requester.display_name,
                "requester_team": resolved_identity.requester.team,
                "requester_glpi_user_id": resolved_identity.requester.glpi_user_id,
                "identity_source": resolved_identity.source,
                "integration_mode": self._merge_modes(
                    response.integration_mode,
                    delivery_result.mode,
                ),
            }
        )
        return WhatsAppInteractionResponse(
            outcome_type="ticket",
            integration_mode=ticket_response.integration_mode,
            requester_role=ticket_response.requester_role,
            requester_external_id=ticket_response.requester_external_id,
            requester_display_name=ticket_response.requester_display_name,
            requester_team=ticket_response.requester_team,
            requester_glpi_user_id=ticket_response.requester_glpi_user_id,
            identity_source=ticket_response.identity_source,
            ticket=ticket_response,
            notes=ticket_response.notes,
        )

    async def correlate(self, request: CorrelationRequest) -> CorrelationResponse:
        events, mode, notes = await self.zabbix_client.find_related_events(
            asset_name=request.asset_name,
            service_name=request.service_name,
            limit=request.limit,
        )
        return CorrelationResponse(mode=mode, events=events, notes=notes)

    async def list_audit_events(
        self,
        *,
        limit: int = 20,
        event_type: str | None = None,
        ticket_id: str | None = None,
        actor_external_id: str | None = None,
    ) -> AuditEventListResponse:
        result = await self.operational_store.list_audit_events(
            limit=limit,
            event_type=event_type,
            ticket_id=ticket_id,
            actor_external_id=actor_external_id,
        )
        applied_filters = {
            key: value
            for key, value in {
                "event_type": event_type,
                "ticket_id": ticket_id,
                "actor_external_id": actor_external_id,
            }.items()
            if value
        }
        return AuditEventListResponse(
            storage_mode=result.storage_mode,
            retention_days=result.retention_days,
            applied_filters=applied_filters,
            events=[
                AuditEventResponse(
                    event_id=event.event_id,
                    created_at=event.created_at.isoformat(),
                    event_type=event.event_type,
                    actor_external_id=event.actor_external_id,
                    actor_role=event.actor_role,
                    ticket_id=event.ticket_id,
                    source_channel=event.source_channel,
                    status=event.status,
                    payload_json=event.payload_json,
                )
                for event in result.events
            ],
            notes=result.notes,
        )

    async def request_automation_job(
        self,
        payload: AutomationJobCreateRequest,
    ) -> AutomationJobResponse:
        validated = self.automation_service.validate_request(
            automation_name=payload.automation_name,
            ticket_id=payload.ticket_id,
            reason=payload.reason,
            parameters=payload.parameters,
        )
        requested_by = payload.requested_by.strip()
        policy = self.automation_service.get_execution_policy(validated.automation_name)
        approval_required = bool(policy["approval_required"])
        approval_status = "pending" if approval_required else "approved"
        execution_status = "awaiting-approval" if approval_required else "queued"
        approval_reason_code = None if approval_required else AUTO_APPROVAL_REASON_CODE
        approval_reason = (
            None
            if approval_required
            else AUTO_APPROVAL_REASON
        )
        approval_updated_at = (
            None if approval_required else datetime.now(timezone.utc).isoformat()
        )
        job = await self.operational_store.create_job_request(
            requested_by=requested_by,
            ticket_id=validated.ticket_id,
            automation_name=validated.automation_name,
            payload_json={
                "request": {
                    "reason": validated.reason,
                    "parameters": validated.parameters,
                },
                "policy": policy,
                "approval": {
                    "status": approval_status,
                    "acted_by": None if approval_required else "system-policy",
                    "reason_code": approval_reason_code,
                    "reason": approval_reason,
                    "updated_at": approval_updated_at,
                },
            },
            approval_status=approval_status,
            execution_status=execution_status,
        )
        queue_mode: str | None = None
        response_notes: list[str] = []

        if approval_required:
            effective_job = job
            response_notes = [
                "Job aguardando aprovacao explicita antes de entrar na fila de execucao."
            ]
        else:
            enqueue_result = await self.job_queue.enqueue_job(job.job_id)
            annotated_job = await self.operational_store.annotate_job_queue(
                job.job_id,
                queue_mode=enqueue_result.queue_mode,
                queue_key=enqueue_result.queue_key,
                notes=enqueue_result.notes,
            )
            effective_job = annotated_job or job
            queue_mode = enqueue_result.queue_mode
            response_notes = enqueue_result.notes

            await self._audit_event(
                event_type="automation_job_approved",
                actor_external_id="system-policy",
                actor_role="automation-policy",
                ticket_id=validated.ticket_id,
                source_channel="api",
                status=effective_job.execution_status,
                payload_json={
                    "automation_name": effective_job.automation_name,
                    "job_id": effective_job.job_id,
                    "approval_mode": policy["approval_mode"],
                    "risk_level": policy["risk_level"],
                    "queue_mode": queue_mode,
                },
            )

        await self._audit_event(
            event_type="automation_job_requested",
            actor_external_id=requested_by,
            actor_role="automation-admin",
            ticket_id=validated.ticket_id,
            source_channel="api",
            status=effective_job.execution_status,
            payload_json={
                "automation_name": effective_job.automation_name,
                "job_id": effective_job.job_id,
                "risk_level": policy["risk_level"],
                "approval_mode": policy["approval_mode"],
                "approval_required": approval_required,
                "queue_mode": queue_mode,
            },
        )
        return self._to_automation_job_response(
            effective_job,
            queue_mode=queue_mode,
            notes=response_notes,
        )

    async def approve_automation_job(
        self,
        job_id: str,
        payload: AutomationJobDecisionRequest,
    ) -> AutomationJobResponse:
        acted_by = payload.acted_by.strip()
        reason_code, reason_label = self._resolve_decision_reason(
            payload.reason_code,
            reason_labels=APPROVAL_REASON_LABELS,
            action_label="approve",
        )
        approved_job = await self.operational_store.approve_job_request(
            job_id,
            acted_by=acted_by,
            reason_code=reason_code,
            reason=reason_label,
        )
        if approved_job is None:
            current_job = await self.operational_store.get_job_request(job_id)
            if current_job is None:
                raise ResourceNotFoundError(f"Job {job_id} nao encontrado.")
            raise ValueError(
                f"Job {job_id} nao esta aguardando aprovacao. Status atual: {current_job.approval_status}/{current_job.execution_status}."
            )

        enqueue_result = await self.job_queue.enqueue_job(job_id)
        annotated_job = await self.operational_store.annotate_job_queue(
            job_id,
            queue_mode=enqueue_result.queue_mode,
            queue_key=enqueue_result.queue_key,
            notes=enqueue_result.notes,
        )
        effective_job = annotated_job or approved_job

        await self._audit_event(
            event_type="automation_job_approved",
            actor_external_id=acted_by,
            actor_role="automation-approver",
            ticket_id=effective_job.ticket_id,
            source_channel="api",
            status=effective_job.execution_status,
            payload_json={
                "automation_name": effective_job.automation_name,
                "job_id": effective_job.job_id,
                "queue_mode": enqueue_result.queue_mode,
                "approval_reason_code": reason_code,
                "approval_reason": reason_label,
            },
        )
        return self._to_automation_job_response(
            effective_job,
            queue_mode=enqueue_result.queue_mode,
            notes=enqueue_result.notes,
        )

    async def reject_automation_job(
        self,
        job_id: str,
        payload: AutomationJobDecisionRequest,
    ) -> AutomationJobResponse:
        acted_by = payload.acted_by.strip()
        reason_code, reason_label = self._resolve_decision_reason(
            payload.reason_code,
            reason_labels=REJECTION_REASON_LABELS,
            action_label="reject",
        )
        rejected_job = await self.operational_store.reject_job_request(
            job_id,
            acted_by=acted_by,
            reason_code=reason_code,
            reason=reason_label,
        )
        if rejected_job is None:
            current_job = await self.operational_store.get_job_request(job_id)
            if current_job is None:
                raise ResourceNotFoundError(f"Job {job_id} nao encontrado.")
            raise ValueError(
                f"Job {job_id} nao esta aguardando aprovacao. Status atual: {current_job.approval_status}/{current_job.execution_status}."
            )

        await self._audit_event(
            event_type="automation_job_rejected",
            actor_external_id=acted_by,
            actor_role="automation-approver",
            ticket_id=rejected_job.ticket_id,
            source_channel="api",
            status=rejected_job.execution_status,
            payload_json={
                "automation_name": rejected_job.automation_name,
                "job_id": rejected_job.job_id,
                "approval_reason_code": reason_code,
                "approval_reason": reason_label,
            },
        )
        return self._to_automation_job_response(rejected_job)

    async def cancel_automation_job(
        self,
        job_id: str,
        payload: AutomationJobDecisionRequest,
    ) -> AutomationJobResponse:
        acted_by = payload.acted_by.strip()
        reason_code, reason_label = self._resolve_decision_reason(
            payload.reason_code,
            reason_labels=CANCELLATION_REASON_LABELS,
            action_label="cancel",
        )
        current_job = await self.operational_store.get_job_request(job_id)
        if current_job is None:
            raise ResourceNotFoundError(f"Job {job_id} nao encontrado.")

        cancelled_job = await self.operational_store.cancel_job_request(
            job_id,
            acted_by=acted_by,
            reason_code=reason_code,
            reason=reason_label,
        )
        if cancelled_job is None:
            raise ValueError(
                f"Job {job_id} nao pode ser cancelado. Status atual: {current_job.approval_status}/{current_job.execution_status}."
            )

        response_notes: list[str] = []
        queue_mode: str | None = None
        removed_count = 0
        if current_job.execution_status == "queued":
            remove_result = await self.job_queue.remove_job(job_id)
            queue_mode = remove_result.queue_mode
            response_notes = remove_result.notes
            removed_count = remove_result.removed_count

        await self._audit_event(
            event_type="automation_job_cancelled",
            actor_external_id=acted_by,
            actor_role="automation-approver",
            ticket_id=cancelled_job.ticket_id,
            source_channel="api",
            status=cancelled_job.execution_status,
            payload_json={
                "automation_name": cancelled_job.automation_name,
                "job_id": cancelled_job.job_id,
                "previous_execution_status": current_job.execution_status,
                "cancellation_reason_code": reason_code,
                "cancellation_reason": reason_label,
                "queue_mode": queue_mode,
                "queue_removed_count": removed_count,
            },
        )
        return self._to_automation_job_response(
            cancelled_job,
            queue_mode=queue_mode,
            notes=response_notes,
        )

    async def get_automation_job(self, job_id: str) -> AutomationJobResponse:
        job = await self.operational_store.get_job_request(job_id)
        if job is None:
            raise ResourceNotFoundError(f"Job {job_id} nao encontrado.")
        return self._to_automation_job_response(job)

    async def list_automation_jobs(
        self,
        *,
        limit: int = 20,
        automation_name: str | None = None,
        ticket_id: str | None = None,
        approval_status: str | None = None,
        execution_status: str | None = None,
    ) -> AutomationJobListResponse:
        result = await self.operational_store.list_job_requests(
            limit=limit,
            automation_name=automation_name,
            ticket_id=ticket_id,
            approval_status=approval_status,
            execution_status=execution_status,
        )
        applied_filters = {
            key: value
            for key, value in {
                "automation_name": automation_name,
                "ticket_id": ticket_id,
                "approval_status": approval_status,
                "execution_status": execution_status,
            }.items()
            if value
        }
        return AutomationJobListResponse(
            storage_mode=result.storage_mode,
            applied_filters=applied_filters,
            jobs=[self._to_automation_job_response(job) for job in result.jobs],
            notes=result.notes,
        )

    async def get_automation_summary(self) -> AutomationSummaryResponse:
        job_summary = await self.operational_store.summarize_job_requests()
        queue_snapshot = await self.job_queue.get_queue_snapshot()

        approval_timeout_minutes = self.operational_store.settings.automation_approval_timeout_minutes
        oldest_pending_approval_expires_at = None
        if (
            approval_timeout_minutes is not None
            and job_summary.oldest_pending_approval_started_at is not None
        ):
            oldest_pending_approval_expires_at = (
                job_summary.oldest_pending_approval_started_at
                + timedelta(minutes=approval_timeout_minutes)
            ).isoformat()

        notes = [*job_summary.notes, *queue_snapshot.notes]
        deduplicated_notes = list(dict.fromkeys(note for note in notes if note))

        return AutomationSummaryResponse(
            storage_mode=job_summary.storage_mode,
            queue_mode=queue_snapshot.queue_mode,
            approval_timeout_minutes=approval_timeout_minutes,
            total_jobs=job_summary.total_jobs,
            approval_status_counts=job_summary.approval_status_counts,
            execution_status_counts=job_summary.execution_status_counts,
            queue_depth=queue_snapshot.queue_depth,
            dead_letter_queue_depth=queue_snapshot.dead_letter_queue_depth,
            oldest_job_created_at=(
                job_summary.oldest_job_created_at.isoformat()
                if job_summary.oldest_job_created_at is not None
                else None
            ),
            oldest_pending_approval_started_at=(
                job_summary.oldest_pending_approval_started_at.isoformat()
                if job_summary.oldest_pending_approval_started_at is not None
                else None
            ),
            oldest_pending_approval_expires_at=oldest_pending_approval_expires_at,
            oldest_queued_job_created_at=(
                job_summary.oldest_queued_job_created_at.isoformat()
                if job_summary.oldest_queued_job_created_at is not None
                else None
            ),
            oldest_running_started_at=(
                job_summary.oldest_running_started_at.isoformat()
                if job_summary.oldest_running_started_at is not None
                else None
            ),
            oldest_retry_scheduled_at=(
                job_summary.oldest_retry_scheduled_at.isoformat()
                if job_summary.oldest_retry_scheduled_at is not None
                else None
            ),
            notes=deduplicated_notes,
        )

    async def process_whatsapp_webhook_messages(
        self,
        messages: list[NormalizedWhatsAppMessage],
        ignored_events: list[str],
    ) -> WhatsAppWebhookProcessingResponse:
        interactions: list[WhatsAppInteractionResponse] = []
        modes: set[str] = set()

        for message in messages:
            response = await self.process_whatsapp_message(message)
            interactions.append(response)
            modes.add(response.integration_mode)

        if not interactions:
            integration_mode = "noop"
        elif len(modes) == 1:
            integration_mode = interactions[0].integration_mode
        else:
            integration_mode = "mixed"

        return WhatsAppWebhookProcessingResponse(
            processed_messages=len(messages),
            interactions=interactions,
            ignored_events=ignored_events,
            integration_mode=integration_mode,
        )

    async def process_meta_webhook_messages(
        self,
        messages: list[NormalizedWhatsAppMessage],
        ignored_events: list[str],
    ) -> WhatsAppWebhookProcessingResponse:
        return await self.process_whatsapp_webhook_messages(messages, ignored_events)

    async def get_ticket(self, ticket_id: str) -> TicketDetailsResponse:
        ticket = await self.glpi_client.get_ticket(ticket_id)
        return TicketDetailsResponse(
            ticket_id=ticket.ticket_id,
            subject=ticket.subject,
            status=ticket.status,
            priority=ticket.priority,
            updated_at=ticket.updated_at,
            requester_glpi_user_id=ticket.requester_glpi_user_id,
            assigned_glpi_user_id=ticket.assigned_glpi_user_id,
            followup_count=ticket.followup_count,
            integration_mode=ticket.mode,
            notes=ticket.notes,
        )

    async def advise_ticket_resolution(self, ticket_id: str) -> TicketResolutionAdviceResponse:
        ticket = await self.glpi_client.get_ticket_analytics_details(ticket_id)
        snapshot = await self.analytics_store.get_snapshot(ticket_id)
        resolution_context = await self.glpi_client.get_ticket_resolution_context(ticket_id, limit=6)

        triage = await self.triage_ticket(
            TicketTriageRequest(
                subject=ticket.subject,
                description=ticket.description or ticket.subject,
                current_category=(snapshot.category_name if snapshot else ticket.category_name),
                current_priority=self._coerce_ticket_priority(ticket.priority),
                asset_name=(snapshot.asset_name if snapshot else None),
                service_name=(snapshot.service_name if snapshot else None),
            )
        )

        summary = triage.summary
        suggested_actions = self._build_resolution_actions(
            triage=triage,
            entries=resolution_context.entries,
        )
        llm_summary, llm_actions, llm_notes = await self._try_llm_resolution_assist(
            ticket=ticket,
            triage=triage,
            snapshot=snapshot,
            entries=resolution_context.entries,
        )

        notes = [
            *ticket.notes,
            *resolution_context.notes,
            *triage.notes,
            *llm_notes,
        ]
        if snapshot is None:
            notes.append(
                "Snapshot analitico nao encontrado para o ticket; usando somente GLPI e heuristicas locais."
            )

        if llm_summary or llm_actions:
            summary = llm_summary or summary
            suggested_actions = llm_actions or suggested_actions

        return TicketResolutionAdviceResponse(
            ticket_id=ticket.ticket_id,
            subject=ticket.subject,
            status=ticket.status,
            priority=ticket.priority,
            category_name=(snapshot.category_name if snapshot else ticket.category_name),
            service_name=(snapshot.service_name if snapshot else None),
            routed_to=(snapshot.routed_to if snapshot else triage.suggested_queue),
            integration_mode=self._merge_modes(ticket.mode, resolution_context.mode),
            summary=summary,
            suggested_actions=suggested_actions,
            resolution_hints=triage.resolution_hints,
            similar_incidents=triage.similar_incidents,
            recent_entries=[
                self._to_ticket_resolution_entry_response(entry)
                for entry in resolution_context.entries
            ],
            notes=notes,
        )

    async def get_registered_identity(self, phone_number: str) -> IdentityLookupResponse:
        return await self.identity_service.get_registered_identity(phone_number)

    async def _run_operator_assistant(
        self,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
    ) -> OperationalAssistantResponse:
        triage = await self.triage_ticket(
            TicketTriageRequest(
                subject=self._build_subject_from_text(message.text, prefix="Operacional WhatsApp"),
                description=self._build_operator_description(message, requester, message.text),
                current_category=(
                    message.category if "category" in message.model_fields_set else None
                ),
                current_priority=(
                    message.priority if "priority" in message.model_fields_set else None
                ),
                asset_name=message.asset_name,
                service_name=message.service_name,
            )
        )
        available_commands = self._available_command_docs(requester.role)
        reply_text = self._build_operator_assistant_reply(
            role=requester.role,
            triage=triage,
        )
        return OperationalAssistantResponse(
            role=requester.role,
            flow_name=self._flow_name_for_role(requester.role),
            reply_text=reply_text,
            triage=triage,
            available_commands=available_commands,
            notes=[
                f"Fluxo operacional aplicado para o papel {requester.role.value}.",
                "Mensagem livre não abriu chamado automaticamente; use /open para registrar um novo chamado.",
            ],
        )

    async def _run_operator_command(
        self,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
    ) -> TechnicianCommandResponse:
        text = message.text.strip()
        parts = text.split(maxsplit=1)
        command_name = parts[0].lstrip("/").lower()
        command_args = parts[1] if len(parts) > 1 else ""

        if not self._is_command_allowed(requester.role, command_name):
            return TechnicianCommandResponse(
                command_name=command_name,
                status="forbidden",
                operation_mode="local",
                reply_text=(
                    f"O papel {requester.role.value} não possui permissão para usar '/{command_name}'."
                ),
                notes=["Comando bloqueado pela política de permissão por papel."],
            )

        if command_name == "help":
            return TechnicianCommandResponse(
                command_name="help",
                status="completed",
                operation_mode="local",
                reply_text=self._build_help_reply(requester.role),
                notes=["Ajuda operacional enviada ao técnico."],
            )

        if command_name == "me":
            return TechnicianCommandResponse(
                command_name="me",
                status="completed",
                operation_mode="local",
                reply_text=(
                    f"Perfil resolvido: {requester.display_name or requester.external_id} | "
                    f"papel={requester.role.value} | time={requester.team or 'n/a'} | "
                    f"glpi_user_id={requester.glpi_user_id or 'n/a'}"
                ),
                notes=["Resumo de identidade operacional retornado ao técnico."],
            )

        if command_name == "open":
            if not command_args:
                return TechnicianCommandResponse(
                    command_name="open",
                    status="invalid",
                    operation_mode="local",
                    reply_text="Uso: /open <descrição do chamado>",
                    notes=["Comando open recebido sem descrição suficiente."],
                )

            open_request = self._build_explicit_operator_open_request(
                message=message,
                requester=requester,
                text=command_args.strip(),
            )
            opened_ticket = await self.open_ticket(open_request)
            return TechnicianCommandResponse(
                command_name="open",
                status="completed",
                operation_mode=opened_ticket.integration_mode,
                reply_text=self._build_operator_open_confirmation_message(opened_ticket),
                opened_ticket=opened_ticket,
                notes=[
                    "Chamado aberto explicitamente por perfil operacional via /open.",
                    *opened_ticket.notes,
                ],
            )

        if command_name == "ticket":
            if not command_args:
                return TechnicianCommandResponse(
                    command_name="ticket",
                    status="invalid",
                    operation_mode="local",
                    reply_text="Uso: /ticket <ticket_id>",
                    notes=["Comando ticket recebido sem identificador."],
                )

            try:
                ticket = await self.get_ticket(command_args.strip())
            except ResourceNotFoundError:
                return TechnicianCommandResponse(
                    command_name="ticket",
                    status="not-found",
                    operation_mode="local",
                    reply_text=f"Ticket {command_args.strip()} não encontrado.",
                    notes=["Consulta operacional retornou ticket inexistente."],
                )

            resolution_advice, resolution_notes = await self._safe_resolution_advice(
                command_args.strip()
            )

            reply_text = (
                f"Ticket {ticket.ticket_id}: assunto='{ticket.subject}', status={ticket.status}, "
                f"prioridade={ticket.priority or 'n/a'}."
            )
            if resolution_advice is not None:
                reply_text = (
                    f"{reply_text} Sugestao: {resolution_advice.summary}"
                )
                if resolution_advice.suggested_actions:
                    reply_text = (
                        f"{reply_text} Acao sugerida: {resolution_advice.suggested_actions[0]}"
                    )
            return TechnicianCommandResponse(
                command_name="ticket",
                status="completed",
                operation_mode=ticket.integration_mode,
                reply_text=reply_text,
                ticket=ticket,
                resolution_advice=resolution_advice,
                notes=[
                    "Consulta operacional de ticket executada com sucesso.",
                    *resolution_notes,
                ],
            )

        if command_name == "correlate":
            if not command_args:
                return TechnicianCommandResponse(
                    command_name="correlate",
                    status="invalid",
                    operation_mode="local",
                    reply_text="Uso: /correlate <ativo-ou-servico>",
                    notes=["Comando correlate recebido sem alvo de busca."],
                )

            correlation = await self.correlate(
                CorrelationRequest(
                    asset_name=command_args.strip(),
                    service_name=command_args.strip(),
                    limit=5,
                )
            )
            events_summary = ", ".join(
                event.summary for event in correlation.events[:3]
            ) or "nenhum evento correlacionado"
            return TechnicianCommandResponse(
                command_name="correlate",
                status="completed",
                operation_mode=correlation.mode,
                reply_text=f"Correlação concluída: {events_summary}.",
                correlation=correlation,
                notes=["Correlação operacional executada para o técnico."],
            )

        if command_name == "comment":
            comment_parts = text.split(maxsplit=2)
            if len(comment_parts) < 3:
                return TechnicianCommandResponse(
                    command_name="comment",
                    status="invalid",
                    operation_mode="local",
                    reply_text="Uso: /comment <ticket_id> <texto>",
                    notes=["Comando comment recebido sem ticket ou conteúdo suficiente."],
                )

            ticket_id = comment_parts[1].strip()
            comment_text = comment_parts[2].strip()
            try:
                result = await self.glpi_client.add_ticket_followup(
                    ticket_id=ticket_id,
                    content=comment_text,
                    author_glpi_user_id=requester.glpi_user_id,
                )
            except ResourceNotFoundError:
                return TechnicianCommandResponse(
                    command_name="comment",
                    status="not-found",
                    operation_mode="local",
                    reply_text=f"Ticket {ticket_id} não encontrado para comentário.",
                    notes=["Comentário operacional não executado porque o ticket não existe."],
                )

            ticket = self._to_ticket_details_response(result.ticket)
            notification_notes: list[str] = []
            operation_mode = result.mode
            reply_text = f"Comentário adicionado ao ticket {ticket.ticket_id}."
            requester_notified = False

            if ticket.requester_glpi_user_id:
                try:
                    requester_identity = await self.identity_service.get_requester_by_glpi_user_id(
                        ticket.requester_glpi_user_id
                    )
                    notification_notes.extend(requester_identity.notes)

                    if requester_identity.requester.phone_number:
                        requester_message = self._build_requester_comment_message(
                            ticket=ticket,
                            operator=requester,
                            comment_text=comment_text,
                        )
                        requester_delivery = await self.whatsapp_client.send_text_message(
                            to_number=requester_identity.requester.phone_number,
                            body=requester_message,
                        )
                        operation_mode = self._merge_modes(result.mode, requester_delivery.mode)
                        notification_notes.extend(requester_delivery.notes)
                        requester_notified = True
                        requester_name = (
                            requester_identity.requester.display_name
                            or requester_identity.requester.external_id
                            or requester_identity.requester.phone_number
                        )
                        reply_text = (
                            f"Comentário adicionado ao ticket {ticket.ticket_id} e enviado ao solicitante {requester_name}."
                        )
                    else:
                        reply_text = (
                            f"Comentário adicionado ao ticket {ticket.ticket_id}, mas o solicitante não possui telefone configurado."
                        )
                        notification_notes.append(
                            "Solicitante do ticket encontrado, mas sem telefone configurado para notificação."
                        )
                except ResourceNotFoundError:
                    reply_text = (
                        f"Comentário adicionado ao ticket {ticket.ticket_id}, mas não consegui localizar o solicitante para notificação."
                    )
                    notification_notes.append(
                        "Solicitante do ticket não pôde ser resolvido para notificação automática."
                    )
                except IntegrationError as exc:
                    reply_text = (
                        f"Comentário adicionado ao ticket {ticket.ticket_id}, mas houve falha ao notificar o solicitante."
                    )
                    notification_notes.append(
                        f"Comentário salvo, mas falhou o envio ao solicitante: {exc}"
                    )
            else:
                notification_notes.append(
                    "Ticket sem solicitante resolvido; notificação automática não enviada."
                )

            await self._audit_event(
                event_type="ticket_followup_added",
                actor_external_id=requester.external_id,
                actor_role=requester.role.value,
                ticket_id=ticket.ticket_id,
                source_channel="whatsapp",
                status="completed",
                payload_json={
                    "integration_mode": operation_mode,
                    "requester_lookup_attempted": bool(ticket.requester_glpi_user_id),
                    "requester_notified": requester_notified,
                },
            )

            resolution_advice, resolution_notes = await self._safe_resolution_advice(
                ticket.ticket_id
            )
            if resolution_advice is not None:
                reply_text = f"{reply_text} Sugestao: {resolution_advice.summary}"
                if resolution_advice.suggested_actions:
                    reply_text = (
                        f"{reply_text} Acao sugerida: {resolution_advice.suggested_actions[0]}"
                    )

            return TechnicianCommandResponse(
                command_name="comment",
                status="completed",
                operation_mode=operation_mode,
                reply_text=reply_text,
                ticket=ticket,
                resolution_advice=resolution_advice,
                notes=[*result.notes, *notification_notes, *resolution_notes],
            )

        if command_name == "status":
            status_parts = text.split(maxsplit=2)
            if len(status_parts) < 3:
                return TechnicianCommandResponse(
                    command_name="status",
                    status="invalid",
                    operation_mode="local",
                    reply_text="Uso: /status <ticket_id> <new|processing|planned|waiting|solved|closed>",
                    notes=["Comando status recebido sem ticket ou status alvo."],
                )

            ticket_id = status_parts[1].strip()
            status_name = status_parts[2].strip().lower()
            if status_name not in PRIVILEGED_ALLOWED_STATUSES:
                return TechnicianCommandResponse(
                    command_name="status",
                    status="invalid",
                    operation_mode="local",
                    reply_text=(
                        "Status inválido. Use: new, processing, planned, waiting, solved ou closed."
                    ),
                    notes=["Mudança de status recusada por valor inválido."],
                )

            if not self._is_status_allowed_for_role(requester.role, status_name):
                return TechnicianCommandResponse(
                    command_name="status",
                    status="forbidden",
                    operation_mode="local",
                    reply_text=(
                        f"O papel {requester.role.value} não pode aplicar o status {status_name}."
                    ),
                    notes=["Mudança de status bloqueada pela política de permissão."],
                )

            try:
                result = await self.glpi_client.update_ticket_status(ticket_id, status_name)
            except ResourceNotFoundError:
                return TechnicianCommandResponse(
                    command_name="status",
                    status="not-found",
                    operation_mode="local",
                    reply_text=f"Ticket {ticket_id} não encontrado para alteração de status.",
                    notes=["Mudança de status não executada porque o ticket não existe."],
                )

            ticket = self._to_ticket_details_response(result.ticket)
            operation_mode = result.mode
            solution_recorded = False
            requester_notified = False

            draft_resolution_advice, resolution_notes = await self._safe_resolution_advice(
                ticket.ticket_id
            )
            resolution_advice = draft_resolution_advice
            solution_notes: list[str] = []
            notification_notes: list[str] = []

            if ticket.status == "solved" and draft_resolution_advice is not None:
                try:
                    solution_result = await self.glpi_client.add_ticket_solution(
                        ticket_id=ticket.ticket_id,
                        content=self._build_ticket_solution_message(
                            ticket=ticket,
                            operator=requester,
                            resolution_advice=draft_resolution_advice,
                        ),
                        author_glpi_user_id=requester.glpi_user_id,
                    )
                    operation_mode = self._merge_modes(operation_mode, solution_result.mode)
                    ticket = self._to_ticket_details_response(solution_result.ticket)
                    solution_recorded = True
                    solution_notes.extend(solution_result.notes)
                except IntegrationError as exc:
                    solution_notes.append(
                        f"Status atualizado, mas falhou o registro da solution no GLPI: {exc}"
                    )

                refreshed_resolution_advice, refreshed_resolution_notes = await self._safe_resolution_advice(
                    ticket.ticket_id
                )
                resolution_notes.extend(refreshed_resolution_notes)
                if refreshed_resolution_advice is not None:
                    resolution_advice = refreshed_resolution_advice

                if ticket.requester_glpi_user_id:
                    try:
                        requester_identity = await self.identity_service.get_requester_by_glpi_user_id(
                            ticket.requester_glpi_user_id
                        )
                        notification_notes.extend(requester_identity.notes)
                        if requester_identity.requester.phone_number:
                            requester_message = self._build_requester_status_message(
                                ticket=ticket,
                                operator=requester,
                                resolution_advice=resolution_advice,
                            )
                            requester_delivery = await self.whatsapp_client.send_text_message(
                                to_number=requester_identity.requester.phone_number,
                                body=requester_message,
                            )
                            operation_mode = self._merge_modes(operation_mode, requester_delivery.mode)
                            notification_notes.extend(requester_delivery.notes)
                            requester_notified = True
                        else:
                            notification_notes.append(
                                "Solicitante do ticket encontrado, mas sem telefone configurado para notificacao de status."
                            )
                    except ResourceNotFoundError:
                        notification_notes.append(
                            "Solicitante do ticket nao pode ser resolvido para notificacao automatica de status."
                        )
                    except IntegrationError as exc:
                        notification_notes.append(
                            f"Status atualizado, mas falhou a notificacao do solicitante: {exc}"
                        )

            await self._audit_event(
                event_type="ticket_status_changed",
                actor_external_id=requester.external_id,
                actor_role=requester.role.value,
                ticket_id=ticket.ticket_id,
                source_channel="whatsapp",
                status=ticket.status,
                payload_json={
                    "integration_mode": operation_mode,
                    "new_status": ticket.status,
                    "solution_recorded": solution_recorded,
                    "requester_notified": requester_notified,
                },
            )

            reply_text = f"Status do ticket {ticket.ticket_id} atualizado para {ticket.status}."
            if requester_notified:
                reply_text = f"{reply_text} Atualizacao enviada ao solicitante."
            if resolution_advice is not None:
                reply_text = f"{reply_text} Sugestao: {resolution_advice.summary}"
                if resolution_advice.suggested_actions:
                    reply_text = (
                        f"{reply_text} Acao sugerida: {resolution_advice.suggested_actions[0]}"
                    )
            return TechnicianCommandResponse(
                command_name="status",
                status="completed",
                operation_mode=operation_mode,
                reply_text=reply_text,
                ticket=ticket,
                resolution_advice=resolution_advice,
                notes=[*result.notes, *solution_notes, *notification_notes, *resolution_notes],
            )

        if command_name == "assign":
            assign_parts = text.split(maxsplit=2)
            if len(assign_parts) < 3:
                return TechnicianCommandResponse(
                    command_name="assign",
                    status="invalid",
                    operation_mode="local",
                    reply_text="Uso: /assign <ticket_id> <telefone-ou-external_id>",
                    notes=["Comando assign recebido sem ticket ou destino."],
                )

            ticket_id = assign_parts[1].strip()
            target_identifier = assign_parts[2].strip()

            try:
                target_identity = await self.identity_service.get_registered_identity_by_identifier(
                    target_identifier
                )
            except ResourceNotFoundError:
                return TechnicianCommandResponse(
                    command_name="assign",
                    status="not-found",
                    operation_mode="local",
                    reply_text=f"Nenhuma identidade encontrada para {target_identifier}.",
                    notes=["Atribuição não executada porque o destino não existe."],
                )

            if target_identity.role not in {
                UserRole.TECHNICIAN,
                UserRole.SUPERVISOR,
                UserRole.ADMIN,
            }:
                return TechnicianCommandResponse(
                    command_name="assign",
                    status="invalid",
                    operation_mode="local",
                    reply_text="A atribuição só pode ser feita para técnico, supervisor ou admin.",
                    notes=["Destino recusado por não ser um perfil operacional."],
                )

            if not target_identity.glpi_user_id:
                return TechnicianCommandResponse(
                    command_name="assign",
                    status="invalid",
                    operation_mode="local",
                    reply_text="O destino não possui glpi_user_id configurado no diretório local.",
                    notes=["Atribuição bloqueada por ausência de vínculo com GLPI."],
                )

            try:
                result = await self.glpi_client.assign_ticket(
                    ticket_id=ticket_id,
                    assignee_glpi_user_id=target_identity.glpi_user_id,
                )
            except ResourceNotFoundError:
                return TechnicianCommandResponse(
                    command_name="assign",
                    status="not-found",
                    operation_mode="local",
                    reply_text=f"Ticket {ticket_id} não encontrado para atribuição.",
                    notes=["Atribuição não executada porque o ticket não existe."],
                )

            ticket = self._to_ticket_details_response(result.ticket)
            await self._audit_event(
                event_type="ticket_assigned",
                actor_external_id=requester.external_id,
                actor_role=requester.role.value,
                ticket_id=ticket.ticket_id,
                source_channel="whatsapp",
                status="completed",
                payload_json={
                    "assignee_external_id": target_identity.external_id,
                    "assignee_role": target_identity.role.value,
                    "integration_mode": result.mode,
                },
            )
            return TechnicianCommandResponse(
                command_name="assign",
                status="completed",
                operation_mode=result.mode,
                reply_text=(
                    f"Ticket {ticket.ticket_id} atribuído para {target_identity.display_name or target_identity.external_id}."
                ),
                ticket=ticket,
                notes=[*result.notes, *target_identity.notes],
            )

        return TechnicianCommandResponse(
            command_name=command_name,
            status="unsupported",
            operation_mode="local",
            reply_text=(
                f"Comando '/{command_name}' não suportado. Use /help para listar os comandos disponíveis."
            ),
            notes=["Comando operacional não suportado."],
        )

    async def _safe_correlate(
        self,
        asset_name: str | None,
        service_name: str | None,
        limit: int,
    ) -> tuple[list, str, list[str]]:
        try:
            return await self.zabbix_client.find_related_events(
                asset_name=asset_name,
                service_name=service_name,
                limit=limit,
            )
        except IntegrationError as exc:
            return [], "degraded", [f"Falha na correlação com Zabbix: {exc}"]

    def _build_ticket_request_from_message(
        self,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
        intake_outcome: UserIntakeOutcome | None = None,
    ) -> TicketOpenRequest:
        summary_source = (
            intake_outcome.summary_text if intake_outcome and intake_outcome.summary_text else message.text
        )
        summary_preview = summary_source.strip().replace("\n", " ")[:80]
        if intake_outcome and intake_outcome.catalog_label:
            subject = f"WhatsApp: {intake_outcome.catalog_label} - {summary_preview}"[:200]
        else:
            subject = f"WhatsApp: {summary_preview}"[:200]

        description_lines = [
            f"Origem: WhatsApp",
            f"Remetente: {requester.display_name or requester.external_id or message.sender_phone}",
            f"Telefone: {message.sender_phone}",
        ]
        if intake_outcome and intake_outcome.catalog_label:
            description_lines.append(f"Tipo de chamado: {intake_outcome.catalog_label}")
        if intake_outcome and intake_outcome.summary_text:
            description_lines.append(f"Resumo informado: {intake_outcome.summary_text}")
        else:
            description_lines.append(f"Mensagem: {message.text}")

        transcript = intake_outcome.transcript if intake_outcome else []
        if transcript:
            description_lines.append("Historico da coleta:")
            description_lines.extend(f"- {entry}" for entry in transcript)

        if message.asset_name:
            description_lines.append(f"Ativo relacionado: {message.asset_name}")
        effective_service_name = (
            message.service_name
            or (intake_outcome.service_name if intake_outcome else None)
        )
        if effective_service_name:
            description_lines.append(f"Servico relacionado: {effective_service_name}")

        payload: dict[str, object] = {
            "subject": subject,
            "description": "\n".join(description_lines),
            "asset_name": message.asset_name,
            "service_name": effective_service_name,
            "requester": requester,
        }
        effective_category = message.category or (intake_outcome.category if intake_outcome else None)
        if effective_category:
            payload["category"] = effective_category
        elif "category" in message.model_fields_set:
            payload["category"] = message.category
        if "priority" in message.model_fields_set:
            payload["priority"] = message.priority
        return TicketOpenRequest(**payload)

    def _build_confirmation_message(self, response: TicketOpenResponse) -> str:
        return (
            f"Seu chamado foi registrado com o número {response.ticket_id}. "
            f"Fila responsável: {response.routed_to}. Status inicial: {response.status}."
        )

    def _build_operator_open_confirmation_message(self, response: TicketOpenResponse) -> str:
        return (
            f"Chamado operacional registrado com o número {response.ticket_id}. "
            f"Fila sugerida: {response.routed_to}. Status inicial: {response.status}."
        )

    def _build_requester_comment_message(
        self,
        ticket: TicketDetailsResponse,
        operator: RequesterIdentity,
        comment_text: str,
    ) -> str:
        operator_name = operator.display_name or operator.external_id or "atendente"
        return (
            f"Atualização do chamado {ticket.ticket_id}: {ticket.subject}\n"
            f"Mensagem do atendente {operator_name}: {comment_text}\n"
            "Se precisar complementar, responda esta conversa informando o número do chamado."
        )

    def _build_requester_status_message(
        self,
        ticket: TicketDetailsResponse,
        operator: RequesterIdentity,
        resolution_advice: TicketResolutionAdviceResponse | None,
    ) -> str:
        operator_name = operator.display_name or operator.external_id or "atendente"
        lines = [
            f"Atualizacao do chamado {ticket.ticket_id}: {ticket.subject}",
            f"O atendente {operator_name} atualizou o status para {ticket.status}.",
        ]
        if resolution_advice is not None:
            lines.append(f"Resumo da resolucao: {resolution_advice.summary}")
            if resolution_advice.suggested_actions:
                lines.append(
                    f"Orientacao registrada: {resolution_advice.suggested_actions[0]}"
                )
        lines.append(
            "Se o problema persistir, responda esta conversa informando o numero do chamado."
        )
        return "\n".join(lines)

    def _build_ticket_solution_message(
        self,
        ticket: TicketDetailsResponse,
        operator: RequesterIdentity,
        resolution_advice: TicketResolutionAdviceResponse,
    ) -> str:
        operator_name = operator.display_name or operator.external_id or "atendente"
        lines = [
            f"Encerramento operacional registrado por {operator_name}.",
            f"Ticket: {ticket.ticket_id} | Assunto: {ticket.subject}",
            f"Resumo da resolucao: {resolution_advice.summary}",
        ]
        for index, action in enumerate(resolution_advice.suggested_actions[:2], start=1):
            lines.append(f"Acao {index}: {action}")
        lines.append("Origem: assistencia de resolucao do backend.")
        return "\n".join(lines)

    def _build_operator_assistant_reply(
        self,
        role: UserRole,
        triage: TicketTriageResponse,
    ) -> str:
        next_step = triage.next_steps[0] if triage.next_steps else "Use /help para ver os comandos disponíveis."
        parts = [
            f"Perfil operacional detectado: {role.value}. Não abri chamado automaticamente.",
            f"Triagem: {triage.summary}",
            f"Próximo passo: {next_step}",
        ]
        if triage.resolution_hints:
            parts.append(f"Sugestao de resolucao: {triage.resolution_hints[0]}")
        if triage.similar_incidents:
            parts.append(f"Caso semelhante: {triage.similar_incidents[0]}")
        parts.append("Se quiser registrar um novo chamado, use /open <descrição>.")
        return " ".join(parts)

    def _to_ticket_resolution_entry_response(self, entry: object) -> TicketResolutionEntryResponse:
        return TicketResolutionEntryResponse(
            source=str(getattr(entry, "source", "unknown")),
            content=str(getattr(entry, "content", "")),
            created_at=getattr(entry, "created_at", None),
            author_glpi_user_id=getattr(entry, "author_glpi_user_id", None),
        )

    async def _safe_resolution_advice(
        self,
        ticket_id: str,
    ) -> tuple[TicketResolutionAdviceResponse | None, list[str]]:
        try:
            return await self.advise_ticket_resolution(ticket_id), []
        except (IntegrationError, ResourceNotFoundError) as exc:
            return None, [f"Assistencia de resolucao indisponivel para o ticket: {exc}"]

    def _coerce_ticket_priority(self, priority_value: str | None) -> TicketPriority | None:
        if not priority_value:
            return None
        try:
            return TicketPriority(priority_value.strip().lower())
        except ValueError:
            return None

    def _build_resolution_actions(
        self,
        *,
        triage: TicketTriageResponse,
        entries: list[object],
    ) -> list[str]:
        actions: list[str] = []
        for entry in entries:
            source = str(getattr(entry, "source", "historico"))
            content = str(getattr(entry, "content", "")).strip()
            if content:
                actions.append(f"Revisar {source} recente: {content}")
                break
        actions.extend(triage.resolution_hints)
        actions.extend(triage.next_steps)

        deduplicated: list[str] = []
        for action in actions:
            if action and action not in deduplicated:
                deduplicated.append(action)
        return deduplicated[:4]

    async def _try_llm_resolution_assist(
        self,
        *,
        ticket: object,
        triage: TicketTriageResponse,
        snapshot: object | None,
        entries: list[object],
    ) -> tuple[str | None, list[str], list[str]]:
        status = self.llm_client.get_status()
        if status.status != "configured":
            return (
                None,
                [],
                [
                    "Camada LLM indisponivel para assistencia de resolucao; mantendo recomendacoes heuristicas."
                ],
            )

        history_lines = [
            self._format_resolution_entry_for_prompt(entry)
            for entry in entries[:6]
        ]
        history_block = "\n".join(history_lines) if history_lines else "- nenhum followup ou solution recente disponivel"
        prompt = (
            "Voce e um assistente de resolucao de helpdesk. "
            "Use apenas o contexto fornecido. "
            "Responda com no maximo 5 linhas. "
            "A primeira linha deve comecar com 'resumo:'. "
            "As proximas linhas devem comecar com 'acao:'. "
            "Nao proponha shell, SSH, automacao nao homologada, reset destrutivo ou mudancas sem evidencia.\n\n"
            f"Ticket: {getattr(ticket, 'ticket_id', 'n/a')}\n"
            f"Assunto: {getattr(ticket, 'subject', 'n/a')}\n"
            f"Status atual: {getattr(ticket, 'status', 'n/a')}\n"
            f"Prioridade: {getattr(ticket, 'priority', None) or 'n/a'}\n"
            f"Categoria: {getattr(snapshot, 'category_name', None) or getattr(ticket, 'category_name', None) or 'n/a'}\n"
            f"Servico: {getattr(snapshot, 'service_name', None) or 'n/a'}\n"
            f"Fila sugerida: {getattr(snapshot, 'routed_to', None) or triage.suggested_queue}\n"
            f"Resumo atual: {triage.summary}\n"
            f"Dicas de resolucao: {' | '.join(triage.resolution_hints) if triage.resolution_hints else 'n/a'}\n"
            f"Casos similares: {' | '.join(triage.similar_incidents) if triage.similar_incidents else 'n/a'}\n"
            "Historico recente do ticket:\n"
            f"{history_block}"
        )

        try:
            result = await self.llm_client.generate_text(
                user_prompt=prompt,
                system_prompt=(
                    "Voce orienta a resolucao segura de tickets com base no historico real do chamado. "
                    "Priorize baixo risco, reaproveite o que ja funcionou e mantenha as acoes objetivas."
                ),
                max_tokens=260,
                temperature=0.1,
            )
        except IntegrationError as exc:
            return None, [], [f"Falha ao gerar assistencia de resolucao com LLM: {exc}"]

        summary: str | None = None
        actions: list[str] = []
        for raw_line in result.content.splitlines():
            line = raw_line.strip()
            lowered = line.lower()
            if lowered.startswith("resumo:") and summary is None:
                summary = line.split(":", maxsplit=1)[1].strip()
            elif lowered.startswith("acao:"):
                action = line.split(":", maxsplit=1)[1].strip()
                if action:
                    actions.append(action)

        if not summary and not actions:
            return None, [], [
                "LLM retornou formato fora do contrato de resolucao; usando recomendacoes heuristicas."
            ]

        return summary, actions[:4], [
            f"Assistencia de resolucao enriquecida pelo provider {result.provider}."
        ]

    def _format_resolution_entry_for_prompt(self, entry: object) -> str:
        source = str(getattr(entry, "source", "historico"))
        created_at = getattr(entry, "created_at", None) or "sem-data"
        content = str(getattr(entry, "content", "")).strip() or "sem-conteudo"
        return f"- [{source}] {created_at}: {content}"

    def _build_explicit_operator_open_request(
        self,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
        text: str,
    ) -> TicketOpenRequest:
        payload: dict[str, object] = {
            "subject": self._build_subject_from_text(text, prefix="Operacional WhatsApp"),
            "description": self._build_operator_description(message, requester, text),
            "asset_name": message.asset_name,
            "service_name": message.service_name,
            "requester": requester,
        }
        if "category" in message.model_fields_set:
            payload["category"] = message.category
        if "priority" in message.model_fields_set:
            payload["priority"] = message.priority
        return TicketOpenRequest(**payload)

    def _build_operator_description(
        self,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
        text: str,
    ) -> str:
        description_lines = [
            "Origem: WhatsApp Operacional",
            f"Operador: {requester.display_name or requester.external_id}",
            f"Papel: {requester.role.value}",
            f"Telefone: {message.sender_phone}",
            f"Mensagem: {text}",
        ]
        if message.asset_name:
            description_lines.append(f"Ativo relacionado: {message.asset_name}")
        if message.service_name:
            description_lines.append(f"Serviço relacionado: {message.service_name}")
        return "\n".join(description_lines)

    def _build_subject_from_text(self, text: str, prefix: str) -> str:
        subject_preview = text.strip().replace("\n", " ")[:80]
        return f"{prefix}: {subject_preview}"

    def _build_help_reply(self, role: UserRole) -> str:
        command_docs = {
            "assign": "/assign <id> <identificador>",
            "comment": "/comment <id> <texto>",
            "correlate": "/correlate <alvo>",
            "help": "/help",
            "me": "/me",
            "open": "/open <descrição>",
            "status": "/status <id> <status>",
            "ticket": "/ticket <id>",
        }
        commands_text = ", ".join(self._available_command_docs(role))
        return (
            f"Comandos disponíveis para {role.value}: {commands_text}. "
            "Mensagens sem '/' entram no assistente operacional; use /open para abrir chamado explicitamente."
        )

    async def _start_user_ticket_finalization(
        self,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
    ) -> UserIntakeOutcome:
        if not requester.glpi_user_id:
            await self.user_intake_service.clear_session(
                message.sender_phone,
                reason="missing_requester_glpi_user_id",
            )
            return UserIntakeOutcome(
                action="assistant",
                flow_name="user_ticket_finalization",
                reply_text=(
                    "Nao consegui identificar seu vinculo com o GLPI para listar os chamados. "
                    "Peça ao time de suporte para revisar seu cadastro."
                ),
                notes=["Fluxo de finalização recusado por ausência de glpi_user_id no solicitante."],
            )

        await self.user_intake_service.clear_session(
            message.sender_phone,
            reason="ticket_finalization_refresh",
        )
        tickets = await self.glpi_client.list_tickets_for_requester(
            requester_glpi_user_id=requester.glpi_user_id,
            include_closed=False,
            limit=5,
            allowed_statuses=USER_FINALIZABLE_STATUSES,
        )
        ticket_options = [
            UserTicketOption(
                ticket_id=ticket.ticket_id,
                subject=ticket.subject,
                status=ticket.status,
                updated_at=ticket.updated_at,
            )
            for ticket in tickets
        ]
        outcome = await self.user_intake_service.start_ticket_finalization(
            phone_number=message.sender_phone,
            requester_display_name=requester.display_name,
            ticket_options=ticket_options,
        )
        await self._audit_event(
            event_type="ticket_finalization_requested",
            actor_external_id=requester.external_id,
            actor_role=requester.role.value,
            source_channel="whatsapp",
            status="completed",
            payload_json={
                "options_available": len(ticket_options),
            },
        )
        return outcome

    async def _finalize_selected_user_ticket(
        self,
        requester: RequesterIdentity,
        selected_ticket_id: str,
        selected_option: str | None,
        base_notes: list[str],
    ) -> OperationalAssistantResponse:
        current_ticket = await self.get_ticket(selected_ticket_id)
        if current_ticket.status not in USER_FINALIZABLE_STATUSES:
            notes = [
                *base_notes,
                (
                    "O ticket selecionado não está mais elegível para finalização pelo usuário. "
                    f"Status atual: {current_ticket.status}."
                ),
            ]
            reply_text = (
                f"O chamado {current_ticket.ticket_id} não pode ser finalizado agora porque está em "
                f"status {current_ticket.status}. Só é possível finalizar chamados que ainda estejam abertos."
            )
            await self._audit_event(
                event_type="ticket_finalization_rejected",
                actor_external_id=requester.external_id,
                actor_role=requester.role.value,
                ticket_id=current_ticket.ticket_id,
                source_channel="whatsapp",
                status=current_ticket.status,
                payload_json={
                    "selected_option": selected_option,
                },
            )
            return OperationalAssistantResponse(
                role=requester.role,
                flow_name="user_ticket_finalization",
                reply_text=reply_text,
                intake_stage="completed",
                selected_option=selected_option,
                notes=notes,
            )

        status_result = await self.glpi_client.update_ticket_status(selected_ticket_id, "closed")
        notes = [*base_notes, *status_result.notes]
        followup_recorded = False
        try:
            followup_result = await self.glpi_client.add_ticket_followup(
                ticket_id=selected_ticket_id,
                content=(
                    "Solicitante informou via WhatsApp que o chamado foi resolvido "
                    "e confirmou a finalização."
                ),
                author_glpi_user_id=requester.glpi_user_id,
            )
            notes.extend(followup_result.notes)
            ticket = followup_result.ticket
            followup_recorded = True
        except IntegrationError as exc:
            ticket = status_result.ticket
            notes.append(
                f"Chamado finalizado, mas não foi possível registrar o comentário de auditoria: {exc}"
            )

        reply_text = (
            f"Chamado {ticket.ticket_id} finalizado com sucesso. "
            f"Status atual: {ticket.status}."
        )
        if selected_option:
            reply_text += f" Seleção confirmada: {selected_option}."

        await self._audit_event(
            event_type="ticket_finalized_by_user",
            actor_external_id=requester.external_id,
            actor_role=requester.role.value,
            ticket_id=ticket.ticket_id,
            source_channel="whatsapp",
            status=ticket.status,
            payload_json={
                "selected_option": selected_option,
                "followup_recorded": followup_recorded,
            },
        )

        return OperationalAssistantResponse(
            role=requester.role,
            flow_name="user_ticket_finalization",
            reply_text=reply_text,
            intake_stage="completed",
            selected_option=selected_option,
            notes=notes,
        )

    def _available_command_docs(self, role: UserRole) -> list[str]:
        command_docs = {
            "assign": "/assign <id> <identificador>",
            "comment": "/comment <id> <texto>",
            "correlate": "/correlate <alvo>",
            "help": "/help",
            "me": "/me",
            "open": "/open <descrição>",
            "status": "/status <id> <status>",
            "ticket": "/ticket <id>",
        }
        available_commands = sorted(ALLOWED_OPERATOR_COMMANDS.get(role, set()))
        return [command_docs[name] for name in available_commands]

    def _flow_name_for_role(self, role: UserRole) -> str:
        if role is UserRole.TECHNICIAN:
            return "technician_operations"
        if role is UserRole.SUPERVISOR:
            return "supervisor_operations"
        if role is UserRole.ADMIN:
            return "admin_operations"
        return "requester_self_service"

    def _to_ticket_details_response(self, ticket: object) -> TicketDetailsResponse:
        return TicketDetailsResponse(
            ticket_id=ticket.ticket_id,
            subject=ticket.subject,
            status=ticket.status,
            priority=ticket.priority,
            updated_at=ticket.updated_at,
            requester_glpi_user_id=ticket.requester_glpi_user_id,
            assigned_glpi_user_id=ticket.assigned_glpi_user_id,
            followup_count=ticket.followup_count,
            integration_mode=ticket.mode,
            notes=ticket.notes,
        )

    def _to_automation_job_response(
        self,
        job: JobRequestRecord,
        *,
        queue_mode: str | None = None,
        notes: list[str] | None = None,
    ) -> AutomationJobResponse:
        policy_section = self._extract_policy_metadata(job.payload_json)
        approval_section = self._extract_approval_metadata(job.payload_json)
        execution_section = self._extract_execution_metadata(job.payload_json)
        return AutomationJobResponse(
            job_id=job.job_id,
            created_at=job.created_at.isoformat(),
            requested_by=job.requested_by,
            ticket_id=job.ticket_id,
            automation_name=job.automation_name,
            risk_level=policy_section["risk_level"],
            approval_mode=policy_section["approval_mode"],
            approval_required=policy_section["approval_required"],
            approval_status=job.approval_status,
            approval_acted_by=approval_section["acted_by"],
            approval_reason_code=approval_section["reason_code"],
            approval_reason=approval_section["reason"],
            approval_updated_at=approval_section["updated_at"],
            execution_status=job.execution_status,
            attempt_count=execution_section["attempt_count"],
            max_attempts=execution_section["max_attempts"],
            retry_scheduled_at=execution_section["retry_scheduled_at"],
            retry_delay_seconds=execution_section["retry_delay_seconds"],
            last_error=execution_section["last_error"],
            dead_lettered_at=execution_section["dead_lettered_at"],
            cancelled_by=execution_section["cancelled_by"],
            cancellation_reason_code=execution_section["cancellation_reason_code"],
            cancellation_reason=execution_section["cancellation_reason"],
            cancelled_at=execution_section["cancelled_at"],
            queue_mode=queue_mode or self._extract_queue_mode(job.payload_json),
            payload_json=job.payload_json,
            notes=notes or [],
        )

    def _extract_queue_mode(self, payload_json: dict[str, object]) -> str | None:
        queue_section = payload_json.get("queue")
        if not isinstance(queue_section, dict):
            return None
        queue_mode = queue_section.get("delivery_mode") or queue_section.get("mode")
        if queue_mode is None:
            return None
        return str(queue_mode)

    def _extract_execution_metadata(self, payload_json: dict[str, object]) -> dict[str, object]:
        execution_section = payload_json.get("execution")
        if not isinstance(execution_section, dict):
            return {
                "attempt_count": 0,
                "max_attempts": 1,
                "retry_scheduled_at": None,
                "retry_delay_seconds": None,
                "last_error": None,
                "dead_lettered_at": None,
                "cancelled_by": None,
                "cancellation_reason_code": None,
                "cancellation_reason": None,
                "cancelled_at": None,
            }

        attempt_count = execution_section.get("attempt_count")
        max_attempts = execution_section.get("max_attempts")
        retry_scheduled_at = execution_section.get("retry_scheduled_at")
        retry_delay_seconds = execution_section.get("retry_delay_seconds")
        last_error = execution_section.get("last_error")
        dead_lettered_at = execution_section.get("dead_lettered_at")
        cancellation = execution_section.get("cancellation")

        if not isinstance(attempt_count, int):
            attempt_count = 0
        if not isinstance(max_attempts, int):
            max_attempts = 1
        if not isinstance(retry_delay_seconds, int):
            retry_delay_seconds = None

        last_error_message: str | None = None
        if isinstance(last_error, dict):
            message = last_error.get("message")
            if message is not None:
                last_error_message = str(message)
        elif last_error is not None:
            last_error_message = str(last_error)

        cancelled_by: str | None = None
        cancellation_reason_code: str | None = None
        cancellation_reason: str | None = None
        cancelled_at: str | None = None
        if isinstance(cancellation, dict):
            acted_by = cancellation.get("acted_by")
            reason_code = cancellation.get("reason_code")
            reason = cancellation.get("reason")
            cancelled_value = cancellation.get("cancelled_at")
            if acted_by is not None:
                cancelled_by = str(acted_by)
            if reason_code is not None:
                cancellation_reason_code = str(reason_code)
            if reason is not None:
                cancellation_reason = str(reason)
            if cancelled_value:
                cancelled_at = str(cancelled_value)

        return {
            "attempt_count": max(attempt_count, 0),
            "max_attempts": max(max_attempts, 1),
            "retry_scheduled_at": (
                str(retry_scheduled_at) if retry_scheduled_at else None
            ),
            "retry_delay_seconds": retry_delay_seconds,
            "last_error": last_error_message,
            "dead_lettered_at": str(dead_lettered_at) if dead_lettered_at else None,
            "cancelled_by": cancelled_by,
            "cancellation_reason_code": cancellation_reason_code,
            "cancellation_reason": cancellation_reason,
            "cancelled_at": cancelled_at,
        }

    def _extract_policy_metadata(self, payload_json: dict[str, object]) -> dict[str, object]:
        policy_section = payload_json.get("policy")
        if not isinstance(policy_section, dict):
            return {
                "risk_level": None,
                "approval_mode": None,
                "approval_required": False,
            }

        approval_required = policy_section.get("approval_required")
        return {
            "risk_level": str(policy_section.get("risk_level")) if policy_section.get("risk_level") else None,
            "approval_mode": str(policy_section.get("approval_mode")) if policy_section.get("approval_mode") else None,
            "approval_required": bool(approval_required),
        }

    def _extract_approval_metadata(self, payload_json: dict[str, object]) -> dict[str, object]:
        approval_section = payload_json.get("approval")
        if not isinstance(approval_section, dict):
            return {
                "acted_by": None,
                "reason_code": None,
                "reason": None,
                "updated_at": None,
            }

        acted_by = approval_section.get("acted_by")
        reason_code = approval_section.get("reason_code")
        reason = approval_section.get("reason")
        updated_at = approval_section.get("updated_at")
        return {
            "acted_by": str(acted_by) if acted_by else None,
            "reason_code": str(reason_code) if reason_code else None,
            "reason": str(reason) if reason else None,
            "updated_at": str(updated_at) if updated_at else None,
        }

    def _resolve_decision_reason(
        self,
        reason_code: str,
        *,
        reason_labels: dict[str, str],
        action_label: str,
    ) -> tuple[str, str]:
        normalized_reason_code = str(reason_code).strip().lower()
        if normalized_reason_code in reason_labels:
            return normalized_reason_code, reason_labels[normalized_reason_code]

        allowed_codes = ", ".join(sorted(reason_labels))
        raise ValueError(
            f"reason_code invalido para {action_label}. Use um de: {allowed_codes}."
        )

    def _should_handle_as_operator_command(
        self,
        text: str,
        role: UserRole,
    ) -> bool:
        if not isinstance(text, str):
            return False
        if not text.strip().startswith("/"):
            return False
        return role in OPERATIONAL_ROLES

    def _is_command_allowed(self, role: UserRole, command_name: str) -> bool:
        return command_name in ALLOWED_OPERATOR_COMMANDS.get(role, set())

    def _is_status_allowed_for_role(self, role: UserRole, status_name: str) -> bool:
        normalized_status = status_name.strip().lower()
        if role is UserRole.TECHNICIAN:
            return normalized_status in TECHNICIAN_ALLOWED_STATUSES
        if role in {UserRole.SUPERVISOR, UserRole.ADMIN}:
            return normalized_status in PRIVILEGED_ALLOWED_STATUSES
        return False

    def _resolve_queue(
        self,
        category: str | None,
        priority: TicketPriority,
    ) -> str:
        return resolve_helpdesk_queue(category, priority)

    def _resolve_source_channel(self, subject: str) -> str:
        normalized_subject = subject.strip().lower()
        if normalized_subject.startswith("operacional whatsapp:"):
            return "whatsapp-operator"
        if normalized_subject.startswith("whatsapp:"):
            return "whatsapp"
        return "api"

    async def _audit_event(
        self,
        *,
        event_type: str,
        actor_external_id: str | None = None,
        actor_role: str | None = None,
        ticket_id: str | None = None,
        source_channel: str,
        status: str,
        payload_json: dict[str, object] | None = None,
    ) -> None:
        await self.operational_store.record_audit_event(
            event_type=event_type,
            actor_external_id=actor_external_id,
            actor_role=actor_role,
            ticket_id=ticket_id,
            source_channel=source_channel,
            status=status,
            payload_json=payload_json,
        )

    def _merge_modes(self, left_mode: str, right_mode: str) -> str:
        if left_mode == right_mode:
            return left_mode
        unique_modes = {left_mode, right_mode}
        if unique_modes == {"live"}:
            return "live"
        if unique_modes == {"mock"}:
            return "mock"
        return "mixed"
