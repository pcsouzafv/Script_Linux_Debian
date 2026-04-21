from dataclasses import dataclass, field
import re

from app.schemas.helpdesk import NormalizedWhatsAppMessage, RequesterIdentity
from app.services.exceptions import IntegrationError
from app.services.llm import LLMClient
from app.services.operational_store import (
    OperationalSessionRecord,
    OperationalStateStore,
    clear_memory_operational_state,
)


@dataclass(frozen=True, slots=True)
class TicketCatalogItem:
    code: str
    label: str
    category: str | None = None
    service_name: str | None = None
    selection_aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UserTicketOption:
    ticket_id: str
    subject: str
    status: str
    updated_at: str | None = None


@dataclass(slots=True)
class UserIntakeSession:
    phone_number: str
    requester_display_name: str | None = None
    flow_name: str = "user_catalog_intake"
    stage: str = "awaiting_catalog"
    selected_catalog_code: str | None = None
    transcript: list[str] = field(default_factory=list)
    ticket_options: list[UserTicketOption] = field(default_factory=list)


@dataclass(slots=True)
class UserIntakeOutcome:
    action: str
    reply_text: str = ""
    flow_name: str = "user_catalog_intake"
    available_options: list[str] = field(default_factory=list)
    intake_stage: str | None = None
    selected_option: str | None = None
    catalog_label: str | None = None
    category: str | None = None
    service_name: str | None = None
    summary_text: str | None = None
    selected_ticket_id: str | None = None
    transcript: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConversationContextDecision:
    action: str = "continue_current_flow"
    notes: list[str] = field(default_factory=list)


CATALOG_ITEMS: tuple[TicketCatalogItem, ...] = (
    TicketCatalogItem(
        code="1",
        label="Acesso / Login / Senha",
        category="acesso",
        service_name="auth",
        selection_aliases=("acesso", "login", "senha", "credencial"),
        keywords=("acesso", "login", "senha", "autentic", "credencial", "bloque"),
    ),
    TicketCatalogItem(
        code="2",
        label="Permissao / Perfil / MFA",
        category="identidade",
        service_name="identidade",
        selection_aliases=("permissao", "perfil", "mfa", "duplo fator"),
        keywords=("permiss", "perfil", "mfa", "identidade", "grupo", "duplo fator"),
    ),
    TicketCatalogItem(
        code="3",
        label="Rede / VPN / Internet",
        category="rede",
        service_name="rede",
        selection_aliases=("rede", "vpn", "internet", "wifi", "wi fi"),
        keywords=("rede", "vpn", "internet", "wifi", "wi fi", "dns", "conecta"),
    ),
    TicketCatalogItem(
        code="4",
        label="Sistema / Aplicacao / ERP",
        category=None,
        service_name="aplicacao",
        selection_aliases=("sistema", "aplicacao", "erp", "glpi"),
        keywords=("erp", "glpi", "sistema", "aplica", "portal"),
    ),
    TicketCatalogItem(
        code="5",
        label="Impressora / Equipamento",
        category="infra",
        service_name="equipamento",
        selection_aliases=("impressora", "equipamento", "notebook", "monitor"),
        keywords=("impressora", "equipamento", "notebook", "monitor", "mouse", "teclado"),
    ),
    TicketCatalogItem(
        code="6",
        label="Servidor / Infraestrutura",
        category="infra",
        service_name="infraestrutura",
        selection_aliases=("servidor", "infra", "infraestrutura", "api", "banco"),
        keywords=("servidor", "infra", "infraestrutura", "container", "api", "mysql", "postgres", "banco"),
    ),
    TicketCatalogItem(
        code="7",
        label="Outro",
        category=None,
        service_name=None,
        selection_aliases=("outro",),
        keywords=(),
    ),
)

GENERIC_MESSAGES = {
    "ajuda",
    "boa noite",
    "boa tarde",
    "bom dia",
    "oi",
    "ola",
    "preciso de ajuda",
    "preciso de suporte",
    "suporte",
}

CANCEL_MESSAGES = {"cancelar", "reiniciar", "resetar"}
TICKET_CLOSE_VERBS = ("finalizar", "encerrar", "fechar", "concluir")
TICKET_CLOSE_NOUNS = ("chamado", "ticket")
CONTEXT_SWITCH_MARKERS = (
    "agora preciso",
    "agora quero",
    "deixa isso",
    "deixa pra la",
    "deixa para la",
    "esquece isso",
    "esquece esse",
    "mudei de assunto",
    "mudei o assunto",
    "na verdade",
    "nao e esse",
    "nao e isso",
    "nao quero isso",
    "outra coisa",
    "outro assunto",
    "outro problema",
    "quero outra coisa",
)
CONTEXT_DECISION_ACTIONS = (
    "continue_current_flow",
    "switch_to_ticket_finalization",
    "switch_to_new_ticket_intake",
    "unclear",
)


def clear_user_intake_sessions() -> None:
    clear_memory_operational_state()


class UserIntakeService:
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        operational_store: OperationalStateStore | None = None,
    ) -> None:
        self.llm_client = llm_client
        if operational_store is None:
            from app.core.config import get_settings

            operational_store = OperationalStateStore(get_settings())
        self.operational_store = operational_store

    async def clear_session(self, phone_number: str, reason: str | None = None) -> None:
        await self.operational_store.delete_session(
            self._normalize_phone(phone_number),
            reason=reason,
        )

    async def has_active_session(self, phone_number: str) -> bool:
        return await self._get_session(phone_number) is not None

    async def has_pending_ticket_finalization(self, phone_number: str) -> bool:
        session = await self._get_session(phone_number)
        return bool(
            session
            and session.flow_name == "user_ticket_finalization"
            and session.stage == "awaiting_ticket_selection"
        )

    async def interpret_active_session(
        self,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
    ) -> ConversationContextDecision:
        session = await self._get_session(message.sender_phone)
        if session is None:
            return ConversationContextDecision()

        rule_decision = self._interpret_context_with_rules(session, message.text)
        if rule_decision.action != "unclear":
            return rule_decision

        llm_decision = await self._interpret_context_with_llm(session, message, requester)
        if llm_decision is not None:
            return llm_decision

        return ConversationContextDecision()

    def matches_ticket_finalization_intent(self, text: str) -> bool:
        normalized_text = self._normalize_text(text)
        if not normalized_text:
            return False
        return any(verb in normalized_text for verb in TICKET_CLOSE_VERBS) and any(
            noun in normalized_text for noun in TICKET_CLOSE_NOUNS
        )

    async def start_ticket_finalization(
        self,
        phone_number: str,
        requester_display_name: str | None,
        ticket_options: list[UserTicketOption],
    ) -> UserIntakeOutcome:
        normalized_phone = self._normalize_phone(phone_number)
        await self.clear_session(normalized_phone, reason="refresh_ticket_finalization")

        if not ticket_options:
            return UserIntakeOutcome(
                action="assistant",
                flow_name="user_ticket_finalization",
                reply_text=(
                    "Nao encontrei chamados seus pendentes para finalizar no momento. "
                    "Se precisar, envie uma nova descricao para abrir outro chamado."
                ),
                notes=["Nenhum ticket elegível foi encontrado para finalização pelo usuário."],
            )

        session = UserIntakeSession(
            phone_number=normalized_phone,
            requester_display_name=requester_display_name,
            flow_name="user_ticket_finalization",
            stage="awaiting_ticket_selection",
            ticket_options=list(ticket_options),
        )
        await self._save_session(session)
        return self._build_ticket_finalization_prompt(session)

    async def handle_ticket_finalization_selection(
        self,
        phone_number: str,
        text: str,
    ) -> UserIntakeOutcome:
        normalized_phone = self._normalize_phone(phone_number)
        session = await self._get_session(normalized_phone)
        if session is None or session.flow_name != "user_ticket_finalization":
            return UserIntakeOutcome(
                action="assistant",
                flow_name="user_ticket_finalization",
                reply_text=(
                    "Nao ha uma seleção pendente para finalizar chamado. "
                    "Envie 'finalizar chamado' para listar seus tickets."
                ),
                notes=["Usuário tentou selecionar ticket sem sessão ativa de finalização."],
            )

        normalized_text = self._normalize_text(text)
        if normalized_text in CANCEL_MESSAGES:
            await self.clear_session(normalized_phone, reason="user_cancelled_finalization")
            return UserIntakeOutcome(
                action="assistant",
                flow_name="user_ticket_finalization",
                reply_text="Finalização cancelada. Se quiser tentar de novo, envie 'finalizar chamado'.",
                notes=["Fluxo de finalização cancelado pelo usuário."],
            )

        selected_option = self._select_ticket_option(session, text)
        if selected_option is None:
            return self._build_ticket_finalization_prompt(
                session,
                extra_note=(
                    "Nao consegui identificar qual chamado voce quer finalizar. "
                    "Responda com o numero da opcao ou com o ID do ticket."
                ),
            )

        await self.clear_session(normalized_phone, reason="ticket_selected_for_finalization")
        return UserIntakeOutcome(
            action="finalize",
            flow_name="user_ticket_finalization",
            selected_ticket_id=selected_option.ticket_id,
            selected_option=self._format_ticket_option(1 + session.ticket_options.index(selected_option), selected_option),
            notes=[f"Usuário escolheu finalizar o ticket {selected_option.ticket_id}."],
        )

    async def handle_message(
        self,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
    ) -> UserIntakeOutcome:
        normalized_phone = self._normalize_phone(message.sender_phone)
        text = message.text.strip()
        normalized_text = self._normalize_text(text)

        if normalized_text in CANCEL_MESSAGES:
            await self.clear_session(normalized_phone, reason="user_cancelled_catalog")
            session = await self._get_or_create_session(normalized_phone, requester.display_name)
            return self._build_catalog_prompt(
                session,
                extra_note="Atendimento reiniciado. Escolha o tipo do chamado ou descreva melhor o problema.",
            )

        session = await self._get_session(normalized_phone)
        if session is None:
            matched_item = self._match_catalog_item(text)
            if matched_item is None and not self._is_low_information(text) and self._is_descriptive(text):
                matched_item = self._fallback_catalog_item()

            if matched_item and self._is_descriptive(text) and not self._is_selection_only(text, matched_item):
                return self._build_open_outcome(matched_item, [text])

            session = await self._get_or_create_session(normalized_phone, requester.display_name)
            session.transcript.append(text)

            if matched_item:
                session.selected_catalog_code = matched_item.code
                session.stage = "awaiting_description"
                await self._save_session(session)
                return self._build_description_prompt(session, matched_item)

            await self._save_session(session)
            return self._build_catalog_prompt(
                session,
                extra_note="Antes de abrir o chamado, preciso classificar o tipo de atendimento.",
            )

        session.requester_display_name = requester.display_name or session.requester_display_name
        session.transcript.append(text)

        if session.stage == "awaiting_catalog":
            matched_item = self._match_catalog_item(text)
            if matched_item is None and self._is_descriptive(text):
                matched_item = self._fallback_catalog_item()

            if matched_item is None:
                await self._save_session(session)
                return self._build_catalog_prompt(
                    session,
                    extra_note="Ainda preciso classificar o tipo do chamado. Escolha uma opcao do catalogo ou descreva melhor.",
                )

            session.selected_catalog_code = matched_item.code
            if self._is_descriptive(text) and not self._is_selection_only(text, matched_item):
                await self.clear_session(normalized_phone, reason="ticket_ready_for_open")
                return self._build_open_outcome(matched_item, session.transcript)

            session.stage = "awaiting_description"
            await self._save_session(session)
            return self._build_description_prompt(session, matched_item)

        selected_item = self._selected_item(session)
        remapped_item = self._match_catalog_item(text)
        if remapped_item and self._is_selection_only(text, remapped_item):
            session.selected_catalog_code = remapped_item.code
            selected_item = remapped_item
            await self._save_session(session)
            return self._build_description_prompt(session, selected_item)

        if not self._is_descriptive(text):
            await self._save_session(session)
            return self._build_description_prompt(
                session,
                selected_item,
                retry=True,
            )

        await self.clear_session(normalized_phone, reason="ticket_ready_for_open")
        return self._build_open_outcome(selected_item, session.transcript)

    def _build_catalog_prompt(
        self,
        session: UserIntakeSession,
        *,
        extra_note: str,
    ) -> UserIntakeOutcome:
        reply_text = (
            f"{extra_note} Responda com o numero da opcao ou descreva o problema com mais detalhes:\n"
            + "\n".join(self.catalog_options())
        )
        return UserIntakeOutcome(
            action="assistant",
            reply_text=reply_text,
            available_options=self.catalog_options(),
            intake_stage="awaiting_catalog",
            notes=["Coleta guiada iniciada para evitar abertura de ticket com contexto insuficiente."],
        )

    def _build_description_prompt(
        self,
        session: UserIntakeSession,
        item: TicketCatalogItem,
        *,
        retry: bool = False,
    ) -> UserIntakeOutcome:
        prefix = "Ainda preciso de mais contexto." if retry else "Entendi o tipo do chamado."
        reply_text = (
            f"{prefix} Vou tratar como '{item.label}'. Agora me diga o que esta acontecendo, onde ocorre e desde quando. "
            "Exemplo: 'Nao consigo acessar o ERP desde 08:10'."
        )
        return UserIntakeOutcome(
            action="assistant",
            reply_text=reply_text,
            available_options=self.catalog_options(),
            intake_stage="awaiting_description",
            selected_option=f"{item.code}. {item.label}",
            catalog_label=item.label,
            category=item.category,
            service_name=item.service_name,
            notes=["Catalogo identificado; aguardando descricao objetiva do problema."],
        )

    def _build_open_outcome(
        self,
        item: TicketCatalogItem,
        transcript: list[str],
    ) -> UserIntakeOutcome:
        return UserIntakeOutcome(
            action="open",
            catalog_label=item.label,
            category=item.category,
            service_name=item.service_name,
            summary_text=self._best_summary(transcript),
            transcript=list(transcript),
            selected_option=f"{item.code}. {item.label}",
            notes=["Coleta guiada concluida; ticket pronto para abertura com contexto consolidado."],
        )

    def _build_ticket_finalization_prompt(
        self,
        session: UserIntakeSession,
        *,
        extra_note: str | None = None,
    ) -> UserIntakeOutcome:
        options = [
            self._format_ticket_option(index + 1, option)
            for index, option in enumerate(session.ticket_options)
        ]
        prefix = extra_note or "Encontrei estes chamados seus para finalizar."
        reply_text = (
            f"{prefix} Responda com o numero da opcao ou com o ID do ticket desejado. "
            "Digite cancelar para sair:\n"
            + "\n".join(options)
        )
        return UserIntakeOutcome(
            action="assistant",
            flow_name="user_ticket_finalization",
            reply_text=reply_text,
            available_options=options,
            intake_stage="awaiting_ticket_selection",
            notes=["Aguardando o usuário escolher qual ticket deve ser finalizado."],
        )

    def catalog_options(self) -> list[str]:
        return [f"{item.code}. {item.label}" for item in CATALOG_ITEMS]

    async def _get_or_create_session(
        self,
        phone_number: str,
        requester_display_name: str | None,
    ) -> UserIntakeSession:
        session = await self._get_session(phone_number)
        if session is None:
            session = UserIntakeSession(
                phone_number=phone_number,
                requester_display_name=requester_display_name,
            )
            await self._save_session(session)
        return session

    async def _get_session(self, phone_number: str) -> UserIntakeSession | None:
        record = await self.operational_store.load_session(self._normalize_phone(phone_number))
        if record is None:
            return None
        return self._record_to_session(record)

    async def _save_session(self, session: UserIntakeSession) -> None:
        await self.operational_store.save_session(self._session_to_record(session))

    def _session_to_record(self, session: UserIntakeSession) -> OperationalSessionRecord:
        return OperationalSessionRecord(
            phone_number=self._normalize_phone(session.phone_number),
            requester_display_name=session.requester_display_name,
            flow_name=session.flow_name,
            stage=session.stage,
            selected_catalog_code=session.selected_catalog_code,
            transcript=list(session.transcript),
            ticket_options=[
                {
                    "ticket_id": option.ticket_id,
                    "subject": option.subject,
                    "status": option.status,
                    "updated_at": option.updated_at,
                }
                for option in session.ticket_options
            ],
        )

    def _record_to_session(self, record: OperationalSessionRecord) -> UserIntakeSession:
        return UserIntakeSession(
            phone_number=record.phone_number,
            requester_display_name=record.requester_display_name,
            flow_name=record.flow_name,
            stage=record.stage,
            selected_catalog_code=record.selected_catalog_code,
            transcript=list(record.transcript),
            ticket_options=[
                UserTicketOption(
                    ticket_id=option["ticket_id"] or "",
                    subject=option["subject"] or "",
                    status=option["status"] or "unknown",
                    updated_at=option.get("updated_at"),
                )
                for option in record.ticket_options
                if option.get("ticket_id")
            ],
        )

    def _interpret_context_with_rules(
        self,
        session: UserIntakeSession,
        text: str,
    ) -> ConversationContextDecision:
        normalized_text = self._normalize_text(text)
        if not normalized_text or normalized_text in CANCEL_MESSAGES:
            return ConversationContextDecision()

        if session.flow_name == "user_ticket_finalization":
            if self._select_ticket_option(session, text) is not None:
                return ConversationContextDecision()
            if self.matches_ticket_finalization_intent(text):
                return ConversationContextDecision(
                    action="switch_to_ticket_finalization",
                    notes=[
                        "Solicitante reforçou a intenção de finalizar chamado; a lista de opções será recarregada."
                    ],
                )
            if self._signals_context_switch(text) or self._is_descriptive(text):
                return ConversationContextDecision(
                    action="switch_to_new_ticket_intake",
                    notes=[
                        "Solicitante saiu do contexto de finalização e voltou a descrever um novo incidente."
                    ],
                )
            return ConversationContextDecision(action="unclear")

        if session.flow_name == "user_catalog_intake":
            if self.matches_ticket_finalization_intent(text):
                return ConversationContextDecision(
                    action="switch_to_ticket_finalization",
                    notes=[
                        "Solicitante mudou do fluxo de abertura para o fluxo de finalização de chamado."
                    ],
                )

            if session.stage == "awaiting_description":
                selected_item = self._selected_item(session)
                remapped_item = self._match_catalog_item(text)
                if (
                    remapped_item
                    and remapped_item.code != selected_item.code
                    and self._signals_context_switch(text)
                    and self._is_descriptive(text)
                ):
                    return ConversationContextDecision(
                        action="switch_to_new_ticket_intake",
                        notes=[
                            "Solicitante mudou o contexto do incidente durante a coleta e o atendimento foi reiniciado com a nova descrição."
                        ],
                    )
                if self._signals_context_switch(text) and self._is_descriptive(text):
                    return ConversationContextDecision(
                        action="switch_to_new_ticket_intake",
                        notes=[
                            "Solicitante sinalizou mudança de assunto durante a coleta e o atendimento foi reiniciado com o novo contexto."
                        ],
                    )

            return ConversationContextDecision()

        return ConversationContextDecision(action="unclear")

    async def _interpret_context_with_llm(
        self,
        session: UserIntakeSession,
        message: NormalizedWhatsAppMessage,
        requester: RequesterIdentity,
    ) -> ConversationContextDecision | None:
        if self.llm_client is None:
            return None

        status = self.llm_client.get_status()
        if status.status != "configured":
            return None

        selected_item = self._selected_item(session) if session.selected_catalog_code else None
        ticket_options = [
            self._format_ticket_option(index + 1, option)
            for index, option in enumerate(session.ticket_options[:5])
        ]
        transcript_preview = "\n".join(f"- {entry}" for entry in session.transcript[-5:]) or "- sem histórico"
        options_preview = "\n".join(ticket_options) or "- sem opções listadas"
        prompt = (
            "Você classifica mudança de contexto em uma conversa de helpdesk via WhatsApp. "
            "Responda com exatamente um dos rótulos abaixo, sem texto extra:\n"
            "continue_current_flow\n"
            "switch_to_ticket_finalization\n"
            "switch_to_new_ticket_intake\n"
            "unclear\n\n"
            f"Fluxo atual: {session.flow_name}\n"
            f"Etapa atual: {session.stage}\n"
            f"Solicitante: {requester.display_name or requester.external_id}\n"
            f"Opção atual do catálogo: {selected_item.label if selected_item else 'n/a'}\n"
            f"Últimas mensagens do contexto:\n{transcript_preview}\n\n"
            f"Opções atualmente exibidas ao usuário:\n{options_preview}\n\n"
            f"Nova mensagem do usuário: {message.text}\n\n"
            "Regras:\n"
            "- Se o usuário continuar a etapa atual, escolha continue_current_flow.\n"
            "- Se o usuário pedir para fechar/finalizar/encerrar chamado, escolha switch_to_ticket_finalization.\n"
            "- Se o usuário abandonar a etapa atual e começar a descrever um novo problema ou novo assunto, escolha switch_to_new_ticket_intake.\n"
            "- Se estiver ambíguo, escolha unclear."
        )

        try:
            result = await self.llm_client.generate_text(
                user_prompt=prompt,
                system_prompt=(
                    "Você só classifica intenção de mudança de contexto para um helpdesk. "
                    "Nunca explique a resposta."
                ),
                max_tokens=12,
                temperature=0.0,
            )
        except IntegrationError:
            return None

        action = self._parse_context_action(result.content)
        if action is None or action == "unclear":
            return None
        return ConversationContextDecision(
            action=action,
            notes=[f"Mudança de contexto interpretada com apoio da IA ({result.provider})."],
        )

    def _parse_context_action(self, content: str) -> str | None:
        lowered = content.strip().lower()
        for action in CONTEXT_DECISION_ACTIONS:
            if action in lowered:
                return action
        return None

    def _select_ticket_option(
        self,
        session: UserIntakeSession,
        text: str,
    ) -> UserTicketOption | None:
        raw_text = text.strip()
        for option in session.ticket_options:
            if raw_text.lower() == option.ticket_id.lower():
                return option

        normalized_text = self._normalize_text(text)
        if normalized_text.isdigit():
            selected_index = int(normalized_text)
            if 1 <= selected_index <= len(session.ticket_options):
                return session.ticket_options[selected_index - 1]
        return None

    def _format_ticket_option(
        self,
        index: int,
        option: UserTicketOption,
    ) -> str:
        subject_preview = option.subject.strip().replace("\n", " ")[:70]
        status = option.status or "unknown"
        if option.updated_at:
            return f"{index}. Ticket {option.ticket_id} | status={status} | {subject_preview} | atualizado em {option.updated_at}"
        return f"{index}. Ticket {option.ticket_id} | status={status} | {subject_preview}"

    def _selected_item(self, session: UserIntakeSession) -> TicketCatalogItem:
        for item in CATALOG_ITEMS:
            if item.code == session.selected_catalog_code:
                return item
        return self._fallback_catalog_item()

    def _fallback_catalog_item(self) -> TicketCatalogItem:
        return CATALOG_ITEMS[-1]

    def _match_catalog_item(self, text: str) -> TicketCatalogItem | None:
        normalized_text = self._normalize_text(text)
        if not normalized_text:
            return None

        for item in CATALOG_ITEMS:
            if normalized_text == item.code:
                return item
            if normalized_text in {self._normalize_text(alias) for alias in item.selection_aliases}:
                return item

        for item in CATALOG_ITEMS:
            if any(keyword in normalized_text for keyword in item.keywords):
                return item
        return None

    def _best_summary(self, transcript: list[str]) -> str:
        for entry in reversed(transcript):
            if not self._is_low_information(entry) and not self._is_catalog_only(entry):
                return entry.strip()
        return transcript[-1].strip() if transcript else "Solicitacao via WhatsApp"

    def _is_selection_only(self, text: str, item: TicketCatalogItem) -> bool:
        normalized_text = self._normalize_text(text)
        aliases = {self._normalize_text(alias) for alias in item.selection_aliases}
        return normalized_text == item.code or normalized_text in aliases

    def _is_catalog_only(self, text: str) -> bool:
        matched_item = self._match_catalog_item(text)
        if matched_item is None:
            return False
        return self._is_selection_only(text, matched_item)

    def _is_low_information(self, text: str) -> bool:
        normalized_text = self._normalize_text(text)
        if normalized_text in GENERIC_MESSAGES:
            return True
        words = normalized_text.split()
        if len(words) <= 2 and self._match_catalog_item(text) is None:
            return True
        if len(normalized_text) < 18 and self._match_catalog_item(text) is None:
            return True
        return False

    def _is_descriptive(self, text: str) -> bool:
        normalized_text = self._normalize_text(text)
        if not normalized_text or normalized_text in GENERIC_MESSAGES:
            return False
        if len(normalized_text) < 18:
            return False
        words = normalized_text.split()
        return len(words) >= 4

    def _signals_context_switch(self, text: str) -> bool:
        normalized_text = self._normalize_text(text)
        normalized_markers = {self._normalize_text(marker) for marker in CONTEXT_SWITCH_MARKERS}
        return any(marker in normalized_text for marker in normalized_markers)

    def _normalize_phone(self, phone_number: str) -> str:
        return "".join(character for character in str(phone_number) if character.isdigit())

    def _normalize_text(self, text: str) -> str:
        lowered = text.strip().lower()
        lowered = re.sub(r"[^a-z0-9à-ÿ\s]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered.strip()