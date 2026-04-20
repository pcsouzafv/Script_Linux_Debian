from app.schemas.helpdesk import (
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
    TicketTriageRequest,
    TicketTriageResponse,
    UserRole,
    WhatsAppInteractionResponse,
    WhatsAppWebhookProcessingResponse,
)
from app.services.exceptions import IntegrationError, ResourceNotFoundError
from app.services.glpi import GLPIClient
from app.services.identity import IdentityService
from app.services.intake import UserIntakeOutcome, UserIntakeService, UserTicketOption
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


class HelpdeskOrchestrator:
    def __init__(
        self,
        glpi_client: GLPIClient,
        zabbix_client: ZabbixClient,
        whatsapp_client: WhatsAppClient,
        identity_service: IdentityService,
        triage_agent: TriageAgent,
        user_intake_service: UserIntakeService,
    ) -> None:
        self.glpi_client = glpi_client
        self.zabbix_client = zabbix_client
        self.whatsapp_client = whatsapp_client
        self.identity_service = identity_service
        self.triage_agent = triage_agent
        self.user_intake_service = user_intake_service

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
            f"Fila sugerida: {triage.suggested_queue}.",
        ]

        return TicketOpenResponse(
            ticket_id=ticket_result.ticket_id,
            status=ticket_result.status,
            routed_to=triage.suggested_queue,
            integration_mode=self._merge_modes(ticket_result.mode, correlation_mode),
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
        if self.user_intake_service.has_active_session(message.sender_phone):
            context_decision = await self.user_intake_service.interpret_active_session(
                message=message,
                requester=resolved_identity.requester,
            )
            if context_decision.action == "switch_to_ticket_finalization":
                self.user_intake_service.clear_session(message.sender_phone)
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
                self.user_intake_service.clear_session(message.sender_phone)
                context_notes = context_decision.notes

        if self.user_intake_service.has_pending_ticket_finalization(message.sender_phone):
            finalization_selection = self.user_intake_service.handle_ticket_finalization_selection(
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

        intake_outcome = self.user_intake_service.handle_message(
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
            return TechnicianCommandResponse(
                command_name="ticket",
                status="completed",
                operation_mode=ticket.integration_mode,
                reply_text=(
                    f"Ticket {ticket.ticket_id}: assunto='{ticket.subject}', status={ticket.status}, "
                    f"prioridade={ticket.priority or 'n/a'}."
                ),
                ticket=ticket,
                notes=["Consulta operacional de ticket executada com sucesso."],
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

            return TechnicianCommandResponse(
                command_name="comment",
                status="completed",
                operation_mode=operation_mode,
                reply_text=reply_text,
                ticket=ticket,
                notes=[*result.notes, *notification_notes],
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
            return TechnicianCommandResponse(
                command_name="status",
                status="completed",
                operation_mode=result.mode,
                reply_text=f"Status do ticket {ticket.ticket_id} atualizado para {ticket.status}.",
                ticket=ticket,
                notes=result.notes,
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

    def _build_operator_assistant_reply(
        self,
        role: UserRole,
        triage: TicketTriageResponse,
    ) -> str:
        next_step = triage.next_steps[0] if triage.next_steps else "Use /help para ver os comandos disponíveis."
        return (
            f"Perfil operacional detectado: {role.value}. Não abri chamado automaticamente. "
            f"Triagem: {triage.summary} Próximo passo: {next_step} "
            f"Se quiser registrar um novo chamado, use /open <descrição>."
        )

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
            self.user_intake_service.clear_session(message.sender_phone)
            return UserIntakeOutcome(
                action="assistant",
                flow_name="user_ticket_finalization",
                reply_text=(
                    "Nao consegui identificar seu vinculo com o GLPI para listar os chamados. "
                    "Peça ao time de suporte para revisar seu cadastro."
                ),
                notes=["Fluxo de finalização recusado por ausência de glpi_user_id no solicitante."],
            )

        self.user_intake_service.clear_session(message.sender_phone)
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
        return self.user_intake_service.start_ticket_finalization(
            phone_number=message.sender_phone,
            requester_display_name=requester.display_name,
            ticket_options=ticket_options,
        )

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

    def _merge_modes(self, left_mode: str, right_mode: str) -> str:
        if left_mode == right_mode:
            return left_mode
        unique_modes = {left_mode, right_mode}
        if unique_modes == {"live"}:
            return "live"
        if unique_modes == {"mock"}:
            return "mock"
        return "mixed"
