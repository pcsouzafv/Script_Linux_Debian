from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.schemas.helpdesk import TicketPriority, TicketTriageRequest
from app.services.glpi import (
    GLPI_REQUEST_TYPE_DIRECT,
    GLPI_REQUEST_TYPE_LABELS,
    GLPI_REQUEST_TYPE_PHONE,
    GLPIClient,
    GLPIResolvedInventoryItem,
    GLPITicketAnalyticsDetails,
)
from app.services.operational_store import AuditEventListResult, AuditEventRecord, OperationalStateStore
from app.services.triage import TriageAgent


ASSET_TOKEN_PATTERN = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+)+\b", re.IGNORECASE)
BACKFILL_ACTOR_ID = "system-glpi-backfill"


@dataclass(slots=True)
class GLPIBackfillDecision:
    ticket_id: str
    subject: str
    status: str
    external_id: str | None = None
    request_type_id: int | None = None
    request_type_name: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    asset_name: str | None = None
    linked_item_type: str | None = None
    linked_item_id: int | None = None
    linked_item_name: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GLPIBackfillSummary:
    processed_count: int
    updated_count: int
    dry_run_count: int
    skipped_count: int
    error_count: int
    results: list[GLPIBackfillDecision] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ResolvedRequestType:
    source_slug: str
    request_type_id: int
    request_type_name: str
    source_note: str


class GLPIHistoricalBackfillService:
    def __init__(
        self,
        glpi_client: GLPIClient,
        operational_store: OperationalStateStore,
        triage_agent: TriageAgent,
    ) -> None:
        self.glpi_client = glpi_client
        self.operational_store = operational_store
        self.triage_agent = triage_agent

    async def backfill_missing_analytics(
        self,
        *,
        ticket_ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        dry_run: bool = True,
    ) -> GLPIBackfillSummary:
        if ticket_ids:
            candidate_ticket_ids = [ticket_id.strip() for ticket_id in ticket_ids if ticket_id.strip()]
        else:
            candidate_ticket_ids = await self.glpi_client.list_ticket_ids(limit=limit, offset=offset)

        results: list[GLPIBackfillDecision] = []
        summary_notes: list[str] = []

        for ticket_id in candidate_ticket_ids:
            decision = await self._backfill_single_ticket(ticket_id=ticket_id, dry_run=dry_run)
            results.append(decision)

        updated_count = sum(1 for result in results if result.status == "updated")
        dry_run_count = sum(1 for result in results if result.status == "dry-run")
        skipped_count = sum(1 for result in results if result.status == "skipped")
        error_count = sum(1 for result in results if result.status == "error")

        if dry_run:
            summary_notes.append("Execução em dry-run; nenhuma atualização foi enviada ao GLPI.")

        return GLPIBackfillSummary(
            processed_count=len(candidate_ticket_ids),
            updated_count=updated_count,
            dry_run_count=dry_run_count,
            skipped_count=skipped_count,
            error_count=error_count,
            results=results,
            notes=summary_notes,
        )

    async def _backfill_single_ticket(
        self,
        *,
        ticket_id: str,
        dry_run: bool,
    ) -> GLPIBackfillDecision:
        try:
            details = await self.glpi_client.get_ticket_analytics_details(ticket_id)
            audit_event_result = await self.operational_store.list_audit_events(
                limit=1,
                event_type="ticket_opened",
                ticket_id=ticket_id,
            )
            audit_event = audit_event_result.events[0] if audit_event_result.events else None
            audit_payload = audit_event.payload_json if audit_event else {}

            notes: list[str] = []
            notes.extend(audit_event_result.notes)

            resolved_request_type = self._resolve_request_type(details, audit_event)
            if resolved_request_type is not None:
                notes.append(resolved_request_type.source_note)

            external_id = details.external_id
            if not external_id and resolved_request_type is not None:
                external_id = self._build_historical_external_id(
                    ticket_id=ticket_id,
                    source_slug=resolved_request_type.source_slug,
                )
                notes.append(f"externalid histórico proposto: {external_id}.")

            request_type_id = None
            request_type_name = None
            if self._should_patch_request_type(details.request_type_id, resolved_request_type):
                request_type_id = resolved_request_type.request_type_id if resolved_request_type else None
                request_type_name = (
                    resolved_request_type.request_type_name if resolved_request_type else None
                )
                if request_type_id is not None:
                    notes.append(
                        "requesttypes_id proposto para backfill: "
                        f"{request_type_id} ({request_type_name})."
                    )

            existing_category_id = (
                details.category_id if isinstance(details.category_id, int) and details.category_id > 0 else None
            )
            category_id = None
            category_name = details.category_name
            if existing_category_id is None:
                category_id, category_name, category_notes = await self._resolve_category_patch(
                    details=details,
                    audit_payload=audit_payload,
                )
                notes.extend(category_notes)

            should_attempt_item_link = (
                external_id != details.external_id
                or request_type_id is not None
                or category_id is not None
            )

            asset_name = self._resolve_asset_name(details, audit_payload)
            linked_item = None
            if asset_name and should_attempt_item_link:
                linked_item = await self.glpi_client.resolve_inventory_item_by_name(asset_name)
                if linked_item is not None:
                    notes.append(
                        "Ativo resolvido para backfill analítico: "
                        f"{linked_item.name} ({linked_item.item_type} {linked_item.item_id})."
                    )
                else:
                    notes.append(
                        f"Ativo {asset_name} não encontrado no inventário do GLPI durante o backfill."
                    )

            if (
                external_id == details.external_id
                and request_type_id is None
                and category_id is None
                and linked_item is None
            ):
                return GLPIBackfillDecision(
                    ticket_id=ticket_id,
                    subject=details.subject,
                    status="skipped",
                    external_id=details.external_id,
                    request_type_id=details.request_type_id,
                    request_type_name=details.request_type_name,
                    category_id=existing_category_id,
                    category_name=details.category_name,
                    notes=notes or ["Ticket já possui os campos analíticos essenciais ou não há contexto seguro para enriquecer."],
                )

            if dry_run:
                if details.status == "closed":
                    notes.append(
                        "Ticket fechado: a API do GLPI tende a bloquear esse patch; para o laboratorio, use ajuste controlado no banco se precisar materializar o backfill."
                    )
                return GLPIBackfillDecision(
                    ticket_id=ticket_id,
                    subject=details.subject,
                    status="dry-run",
                    external_id=external_id,
                    request_type_id=request_type_id or details.request_type_id,
                    request_type_name=request_type_name or details.request_type_name,
                    category_id=category_id or existing_category_id,
                    category_name=category_name or details.category_name,
                    asset_name=asset_name,
                    linked_item_type=(linked_item.item_type if linked_item else None),
                    linked_item_id=(linked_item.item_id if linked_item else None),
                    linked_item_name=(linked_item.name if linked_item else None),
                    notes=notes,
                )

            if details.status == "closed":
                notes.append(
                    "Ticket fechado: a API do GLPI bloqueia a atualização analítica; lote mantido sem erro para esse ticket."
                )
                return GLPIBackfillDecision(
                    ticket_id=ticket_id,
                    subject=details.subject,
                    status="skipped",
                    external_id=details.external_id,
                    request_type_id=details.request_type_id,
                    request_type_name=details.request_type_name,
                    category_id=existing_category_id,
                    category_name=details.category_name,
                    asset_name=asset_name,
                    notes=notes,
                )

            patch_result = await self.glpi_client.apply_ticket_analytics_patch(
                ticket_id=ticket_id,
                external_id=(external_id if external_id != details.external_id else None),
                request_type_id=request_type_id,
                category_id=category_id,
                category_name=category_name,
                linked_item=linked_item,
            )

            await self.operational_store.record_audit_event(
                event_type="ticket_analytics_backfilled",
                actor_external_id=BACKFILL_ACTOR_ID,
                actor_role="system",
                ticket_id=ticket_id,
                source_channel="maintenance",
                status=patch_result.status,
                payload_json={
                    "external_id": patch_result.external_id,
                    "request_type_id": patch_result.request_type_id,
                    "request_type_name": patch_result.request_type_name,
                    "category_id": patch_result.category_id,
                    "category_name": patch_result.category_name,
                    "asset_name": asset_name,
                    "linked_item_type": patch_result.linked_item_type,
                    "linked_item_id": patch_result.linked_item_id,
                    "linked_item_name": patch_result.linked_item_name,
                },
            )

            return GLPIBackfillDecision(
                ticket_id=ticket_id,
                subject=details.subject,
                status="updated",
                external_id=patch_result.external_id,
                request_type_id=patch_result.request_type_id,
                request_type_name=patch_result.request_type_name,
                category_id=patch_result.category_id,
                category_name=patch_result.category_name,
                asset_name=asset_name,
                linked_item_type=patch_result.linked_item_type,
                linked_item_id=patch_result.linked_item_id,
                linked_item_name=patch_result.linked_item_name,
                notes=[*notes, *patch_result.notes],
            )
        except Exception as exc:  # pragma: no cover - defensive guard for batch execution
            return GLPIBackfillDecision(
                ticket_id=ticket_id,
                subject=f"Ticket {ticket_id}",
                status="error",
                notes=[f"Falha ao processar backfill analítico do ticket: {exc}"],
            )

    async def _resolve_category_patch(
        self,
        *,
        details: GLPITicketAnalyticsDetails,
        audit_payload: dict[str, object],
    ) -> tuple[int | None, str | None, list[str]]:
        notes: list[str] = []

        audit_category_id = self._normalize_int(audit_payload.get("glpi_category_id"))
        audit_category_name = self._normalize_optional_text(
            audit_payload.get("glpi_category_name")
        ) or self._normalize_optional_text(audit_payload.get("category"))
        if audit_category_id is not None:
            notes.append(
                "Categoria reutilizada da auditoria operacional: "
                f"{audit_category_id} ({audit_category_name or 'sem nome'})."
            )
            return audit_category_id, audit_category_name, notes

        if audit_category_name:
            resolved_category = await self.glpi_client.resolve_category_by_name(audit_category_name)
            if resolved_category is not None:
                notes.append(
                    "Categoria resolvida a partir da auditoria operacional: "
                    f"{resolved_category.name} ({resolved_category.category_id})."
                )
                return resolved_category.category_id, resolved_category.name, notes
            notes.append(
                f"Categoria {audit_category_name} presente na auditoria, mas não encontrada no GLPI."
            )

        triage_request = TicketTriageRequest(
            subject=details.subject,
            description=details.description or details.subject,
            current_priority=self._map_priority_name(details.priority),
            asset_name=self._resolve_asset_name(details, audit_payload),
            service_name=self._normalize_optional_text(audit_payload.get("service_name")),
        )
        triage = await self.triage_agent.triage(triage_request)
        if not triage.resolved_category:
            notes.append("Triagem por regras não encontrou categoria segura para o backfill.")
            return None, None, notes

        resolved_category = await self.glpi_client.resolve_category_by_name(triage.resolved_category)
        if resolved_category is None:
            notes.append(
                f"Triagem sugeriu categoria {triage.resolved_category}, mas ela não existe no GLPI."
            )
            return None, triage.resolved_category, notes

        notes.append(
            "Categoria sugerida pela triagem por regras: "
            f"{resolved_category.name} ({resolved_category.category_id})."
        )
        return resolved_category.category_id, resolved_category.name, notes

    def _resolve_request_type(
        self,
        details: GLPITicketAnalyticsDetails,
        audit_event: AuditEventRecord | None,
    ) -> _ResolvedRequestType | None:
        if audit_event is not None:
            audit_payload = audit_event.payload_json
            audit_request_type_id = self._normalize_int(audit_payload.get("glpi_request_type_id"))
            if audit_request_type_id in GLPI_REQUEST_TYPE_LABELS:
                source_slug = self._source_slug_from_channel(
                    audit_event.source_channel,
                    details.subject,
                    details.description,
                )
                return _ResolvedRequestType(
                    source_slug=source_slug,
                    request_type_id=audit_request_type_id,
                    request_type_name=(
                        self._normalize_optional_text(audit_payload.get("glpi_request_type_name"))
                        or GLPI_REQUEST_TYPE_LABELS[audit_request_type_id]
                    ),
                    source_note=(
                        "Origem do ticket recuperada da auditoria operacional: "
                        f"{audit_event.source_channel} -> {GLPI_REQUEST_TYPE_LABELS[audit_request_type_id]}."
                    ),
                )

            if audit_event.source_channel == "whatsapp":
                return _ResolvedRequestType(
                    source_slug=self._source_slug_from_channel(
                        "whatsapp",
                        details.subject,
                        details.description,
                    ),
                    request_type_id=GLPI_REQUEST_TYPE_PHONE,
                    request_type_name=GLPI_REQUEST_TYPE_LABELS[GLPI_REQUEST_TYPE_PHONE],
                    source_note="Origem do ticket inferida da auditoria operacional: whatsapp.",
                )

            if audit_event.source_channel == "api":
                return _ResolvedRequestType(
                    source_slug="api",
                    request_type_id=GLPI_REQUEST_TYPE_DIRECT,
                    request_type_name=GLPI_REQUEST_TYPE_LABELS[GLPI_REQUEST_TYPE_DIRECT],
                    source_note="Origem do ticket inferida da auditoria operacional: api.",
                )

        subject = details.subject.strip().lower()
        description = (details.description or "").strip().lower()
        if subject.startswith("operacional whatsapp:"):
            return _ResolvedRequestType(
                source_slug="whatsapp-operator",
                request_type_id=GLPI_REQUEST_TYPE_PHONE,
                request_type_name=GLPI_REQUEST_TYPE_LABELS[GLPI_REQUEST_TYPE_PHONE],
                source_note="Origem inferida pelo prefixo do assunto: Operacional WhatsApp.",
            )
        if subject.startswith("whatsapp:") or "origem: whatsapp" in description:
            return _ResolvedRequestType(
                source_slug="whatsapp",
                request_type_id=GLPI_REQUEST_TYPE_PHONE,
                request_type_name=GLPI_REQUEST_TYPE_LABELS[GLPI_REQUEST_TYPE_PHONE],
                source_note="Origem inferida pelo assunto/descrição do ticket histórico: WhatsApp.",
            )
        return None

    def _should_patch_request_type(
        self,
        current_request_type_id: int | None,
        resolved_request_type: _ResolvedRequestType | None,
    ) -> bool:
        if resolved_request_type is None:
            return False
        if current_request_type_id is None:
            return True
        if current_request_type_id == resolved_request_type.request_type_id:
            return False
        return current_request_type_id in {0, 1}

    def _resolve_asset_name(
        self,
        details: GLPITicketAnalyticsDetails,
        audit_payload: dict[str, object],
    ) -> str | None:
        audit_asset_name = self._normalize_optional_text(audit_payload.get("asset_name"))
        if audit_asset_name:
            return audit_asset_name

        candidates = self._extract_asset_candidates(details.subject)
        candidates.extend(self._extract_asset_candidates(details.description or ""))
        for candidate in candidates:
            return candidate
        return None

    def _extract_asset_candidates(self, text: str) -> list[str]:
        seen: set[str] = set()
        candidates: list[str] = []
        for match in ASSET_TOKEN_PATTERN.findall(text):
            normalized = match.strip()
            lowered = normalized.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            candidates.append(normalized)
        return candidates

    def _build_historical_external_id(self, *, ticket_id: str, source_slug: str) -> str:
        return f"helpdesk-{source_slug}-historical-{ticket_id}"

    def _source_slug_from_channel(
        self,
        source_channel: str | None,
        subject: str,
        description: str | None,
    ) -> str:
        if (source_channel or "").strip().lower() == "api":
            return "api"
        if subject.strip().lower().startswith("operacional whatsapp:"):
            return "whatsapp-operator"
        if subject.strip().lower().startswith("whatsapp:") or "origem: whatsapp" in (
            description or ""
        ).strip().lower():
            return "whatsapp"
        if (source_channel or "").strip().lower() == "whatsapp":
            return "whatsapp"
        return "legacy"

    def _map_priority_name(self, priority_name: str | None) -> TicketPriority | None:
        normalized = (priority_name or "").strip().lower()
        mapping = {
            "low": TicketPriority.LOW,
            "medium": TicketPriority.MEDIUM,
            "high": TicketPriority.HIGH,
            "critical": TicketPriority.CRITICAL,
            "very-low": TicketPriority.LOW,
            "very-high": TicketPriority.HIGH,
            "major": TicketPriority.CRITICAL,
        }
        return mapping.get(normalized)

    def _normalize_optional_text(self, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _normalize_int(self, value: object) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None