from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re

from app.services.glpi import GLPI_REQUEST_TYPE_PHONE, GLPIClient, GLPITicketAnalyticsDetails
from app.services.operational_store import AuditEventRecord, OperationalStateStore
from app.services.ticket_analytics_store import (
    TicketAnalyticsSnapshotRecord,
    TicketAnalyticsStore,
)


ASSET_TOKEN_PATTERN = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+)+\b", re.IGNORECASE)


@dataclass(slots=True)
class GLPIAnalyticsSyncItemResult:
    ticket_id: str
    subject: str
    status: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GLPIAnalyticsSyncSummary:
    processed_count: int
    synced_count: int
    error_count: int
    results: list[GLPIAnalyticsSyncItemResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class GLPIAnalyticsSyncService:
    def __init__(
        self,
        glpi_client: GLPIClient,
        operational_store: OperationalStateStore,
        analytics_store: TicketAnalyticsStore,
    ) -> None:
        self.glpi_client = glpi_client
        self.operational_store = operational_store
        self.analytics_store = analytics_store

    async def sync_ticket_snapshots(
        self,
        *,
        ticket_ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> GLPIAnalyticsSyncSummary:
        if ticket_ids:
            candidate_ticket_ids = [ticket_id.strip() for ticket_id in ticket_ids if ticket_id.strip()]
        else:
            candidate_ticket_ids = await self.glpi_client.list_ticket_ids(limit=limit, offset=offset)

        results: list[GLPIAnalyticsSyncItemResult] = []
        for ticket_id in candidate_ticket_ids:
            results.append(await self._sync_single_ticket(ticket_id))

        return GLPIAnalyticsSyncSummary(
            processed_count=len(candidate_ticket_ids),
            synced_count=sum(1 for item in results if item.status == "synced"),
            error_count=sum(1 for item in results if item.status == "error"),
            results=results,
        )

    async def _sync_single_ticket(self, ticket_id: str) -> GLPIAnalyticsSyncItemResult:
        try:
            details = await self.glpi_client.get_ticket_analytics_details(ticket_id)
            audit_result = await self.operational_store.list_audit_events(
                limit=1,
                event_type="ticket_opened",
                ticket_id=ticket_id,
            )
            audit_event = audit_result.events[0] if audit_result.events else None
            snapshot = self._build_snapshot(details, audit_event)
            await self.analytics_store.upsert_snapshot(snapshot)
            notes = []
            notes.extend(audit_result.notes)
            if audit_event is not None:
                notes.append("Snapshot enriquecido com o evento operacional ticket_opened.")
            else:
                notes.append("Snapshot enriquecido sem ticket_opened durável; aplicado fallback heurístico mínimo.")
            return GLPIAnalyticsSyncItemResult(
                ticket_id=ticket_id,
                subject=details.subject,
                status="synced",
                notes=notes,
            )
        except Exception as exc:  # pragma: no cover
            return GLPIAnalyticsSyncItemResult(
                ticket_id=ticket_id,
                subject=f"Ticket {ticket_id}",
                status="error",
                notes=[f"Falha ao sincronizar snapshot analítico: {exc}"],
            )

    def _build_snapshot(
        self,
        details: GLPITicketAnalyticsDetails,
        audit_event: AuditEventRecord | None,
    ) -> TicketAnalyticsSnapshotRecord:
        audit_payload = audit_event.payload_json if audit_event else {}
        asset_name = self._optional_string(audit_payload.get("asset_name")) or self._extract_asset_name(
            details.subject,
            details.description,
        )
        service_name = self._optional_string(audit_payload.get("service_name")) or self._extract_labeled_value(
            details.description,
            "Servico relacionado",
        )
        category_name = (
            details.category_name
            or self._optional_string(audit_payload.get("glpi_category_name"))
            or self._optional_string(audit_payload.get("category"))
        )
        category_id = details.category_id or self._normalize_int(audit_payload.get("glpi_category_id"))
        request_type_name = details.request_type_name or self._derive_request_type_name(details)
        source_channel = (
            audit_event.source_channel if audit_event is not None else self._derive_source_channel(details)
        )

        return TicketAnalyticsSnapshotRecord(
            ticket_id=details.ticket_id,
            subject=details.subject,
            description=details.description,
            status=details.status,
            priority=details.priority,
            requester_glpi_user_id=details.requester_glpi_user_id,
            assigned_glpi_user_id=details.assigned_glpi_user_id,
            external_id=details.external_id,
            request_type_id=details.request_type_id,
            request_type_name=request_type_name,
            category_id=category_id,
            category_name=category_name,
            asset_name=asset_name,
            service_name=service_name,
            source_channel=source_channel,
            routed_to=self._optional_string(audit_payload.get("routed_to")),
            correlation_event_count=self._normalize_int(audit_payload.get("correlation_event_count")) or 0,
            source_updated_at=self._parse_timestamp(details.updated_at),
            source_audit_created_at=(audit_event.created_at if audit_event is not None else None),
            attributes_json={
                "audit_payload": audit_payload,
                "detail_notes": details.notes,
            },
        )

    def _derive_request_type_name(self, details: GLPITicketAnalyticsDetails) -> str | None:
        if details.request_type_id == GLPI_REQUEST_TYPE_PHONE:
            return "Phone"
        if details.request_type_id is None:
            return None
        return "Direct"

    def _derive_source_channel(self, details: GLPITicketAnalyticsDetails) -> str | None:
        subject = details.subject.strip().lower()
        description = (details.description or "").strip().lower()
        if subject.startswith("operacional whatsapp:"):
            return "whatsapp"
        if subject.startswith("whatsapp:") or "origem: whatsapp" in description:
            return "whatsapp"
        if details.request_type_id == GLPI_REQUEST_TYPE_PHONE:
            return "whatsapp"
        return "api"

    def _extract_asset_name(self, subject: str, description: str | None) -> str | None:
        for text in (subject, description or ""):
            for match in ASSET_TOKEN_PATTERN.findall(text):
                return match.strip()
        return None

    def _extract_labeled_value(self, description: str | None, label: str) -> str | None:
        normalized_description = description or ""
        prefix = f"{label}:"
        for raw_line in normalized_description.splitlines():
            line = raw_line.strip()
            if not line.startswith(prefix):
                continue
            return self._optional_string(line[len(prefix) :])
        return None

    def _parse_timestamp(self, value: str | None) -> datetime | None:
        normalized = self._optional_string(value)
        if not normalized:
            return None
        try:
            return datetime.fromisoformat(normalized.replace(" ", "T"))
        except ValueError:
            return None

    def _optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _normalize_int(self, value: object) -> int | None:
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            return parsed if parsed > 0 else None
        return None