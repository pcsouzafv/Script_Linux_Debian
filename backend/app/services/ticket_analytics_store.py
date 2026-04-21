from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any

from app.core.config import Settings

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None


SAFE_SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_OPERATIONAL_SCHEMA = "helpdesk_platform"


@dataclass(slots=True)
class TicketAnalyticsSnapshotRecord:
    ticket_id: str
    subject: str
    description: str | None
    status: str
    priority: str | None
    requester_glpi_user_id: int | None
    assigned_glpi_user_id: int | None
    external_id: str | None
    request_type_id: int | None
    request_type_name: str | None
    category_id: int | None
    category_name: str | None
    asset_name: str | None
    service_name: str | None
    source_channel: str | None
    routed_to: str | None
    correlation_event_count: int = 0
    source_updated_at: datetime | None = None
    source_audit_created_at: datetime | None = None
    snapshot_created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attributes_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TicketAnalyticsSnapshotListResult:
    snapshots: list[TicketAnalyticsSnapshotRecord]
    storage_mode: str
    notes: list[str] = field(default_factory=list)


_MEMORY_TICKET_ANALYTICS: dict[str, TicketAnalyticsSnapshotRecord] = {}


class TicketAnalyticsStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.schema_name = settings.operational_postgres_schema or DEFAULT_OPERATIONAL_SCHEMA
        if not SAFE_SCHEMA_NAME.fullmatch(self.schema_name):
            raise ValueError(
                "HELPDESK_OPERATIONAL_POSTGRES_SCHEMA deve usar apenas letras, numeros e underscore."
            )

    async def upsert_snapshot(
        self,
        record: TicketAnalyticsSnapshotRecord,
    ) -> TicketAnalyticsSnapshotRecord:
        normalized = self._normalize_record(record)
        _MEMORY_TICKET_ANALYTICS[normalized.ticket_id] = self._clone_record(normalized)

        connection = await self._open_connection()
        if connection is not None:
            try:
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema_name}.ticket_analytics_snapshot (
                        ticket_id,
                        subject,
                        description,
                        status,
                        priority,
                        requester_glpi_user_id,
                        assigned_glpi_user_id,
                        external_id,
                        request_type_id,
                        request_type_name,
                        category_id,
                        category_name,
                        asset_name,
                        service_name,
                        source_channel,
                        routed_to,
                        correlation_event_count,
                        source_updated_at,
                        source_audit_created_at,
                        snapshot_created_at,
                        snapshot_updated_at,
                        attributes_json
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                        $15, $16, $17, $18, $19, $20, $21, $22::jsonb
                    )
                    ON CONFLICT (ticket_id) DO UPDATE SET
                        subject = EXCLUDED.subject,
                        description = EXCLUDED.description,
                        status = EXCLUDED.status,
                        priority = EXCLUDED.priority,
                        requester_glpi_user_id = EXCLUDED.requester_glpi_user_id,
                        assigned_glpi_user_id = EXCLUDED.assigned_glpi_user_id,
                        external_id = EXCLUDED.external_id,
                        request_type_id = EXCLUDED.request_type_id,
                        request_type_name = EXCLUDED.request_type_name,
                        category_id = EXCLUDED.category_id,
                        category_name = EXCLUDED.category_name,
                        asset_name = EXCLUDED.asset_name,
                        service_name = EXCLUDED.service_name,
                        source_channel = EXCLUDED.source_channel,
                        routed_to = EXCLUDED.routed_to,
                        correlation_event_count = EXCLUDED.correlation_event_count,
                        source_updated_at = EXCLUDED.source_updated_at,
                        source_audit_created_at = EXCLUDED.source_audit_created_at,
                        snapshot_updated_at = EXCLUDED.snapshot_updated_at,
                        attributes_json = EXCLUDED.attributes_json
                    """,
                    normalized.ticket_id,
                    normalized.subject,
                    normalized.description,
                    normalized.status,
                    normalized.priority,
                    normalized.requester_glpi_user_id,
                    normalized.assigned_glpi_user_id,
                    normalized.external_id,
                    normalized.request_type_id,
                    normalized.request_type_name,
                    normalized.category_id,
                    normalized.category_name,
                    normalized.asset_name,
                    normalized.service_name,
                    normalized.source_channel,
                    normalized.routed_to,
                    normalized.correlation_event_count,
                    normalized.source_updated_at,
                    normalized.source_audit_created_at,
                    normalized.snapshot_created_at,
                    normalized.snapshot_updated_at,
                    json.dumps(normalized.attributes_json, ensure_ascii=True),
                )
            finally:
                await connection.close()

        return self._clone_record(normalized)

    async def get_snapshot(self, ticket_id: str) -> TicketAnalyticsSnapshotRecord | None:
        normalized_ticket_id = self._optional_string(ticket_id)
        if not normalized_ticket_id:
            return None

        connection = await self._open_connection()
        if connection is not None:
            try:
                row = await connection.fetchrow(
                    f"""
                    SELECT *
                    FROM {self.schema_name}.ticket_analytics_snapshot
                    WHERE ticket_id = $1
                    """,
                    normalized_ticket_id,
                )
            finally:
                await connection.close()

            if row is not None:
                record = self._record_from_row(row)
                _MEMORY_TICKET_ANALYTICS[normalized_ticket_id] = self._clone_record(record)
                return record

        cached = _MEMORY_TICKET_ANALYTICS.get(normalized_ticket_id)
        if cached is None:
            return None
        return self._clone_record(cached)

    async def list_snapshots(
        self,
        *,
        limit: int = 20,
        category_name: str | None = None,
        source_channel: str | None = None,
    ) -> TicketAnalyticsSnapshotListResult:
        normalized_limit = max(1, min(limit, 200))
        normalized_category_name = self._optional_string(category_name)
        normalized_source_channel = self._optional_string(source_channel)

        connection = await self._open_connection()
        if connection is not None:
            try:
                rows = await connection.fetch(
                    f"""
                    SELECT *
                    FROM {self.schema_name}.ticket_analytics_snapshot
                    WHERE ($1::text IS NULL OR category_name = $1)
                      AND ($2::text IS NULL OR source_channel = $2)
                    ORDER BY COALESCE(source_updated_at, snapshot_updated_at) DESC, ticket_id DESC
                    LIMIT $3
                    """,
                    normalized_category_name,
                    normalized_source_channel,
                    normalized_limit,
                )
            finally:
                await connection.close()

            return TicketAnalyticsSnapshotListResult(
                snapshots=[self._record_from_row(row) for row in rows],
                storage_mode="postgres",
            )

        snapshots = [
            self._clone_record(record)
            for record in sorted(
                _MEMORY_TICKET_ANALYTICS.values(),
                key=lambda item: (
                    item.source_updated_at or item.snapshot_updated_at,
                    item.ticket_id,
                ),
                reverse=True,
            )
            if (
                (normalized_category_name is None or record.category_name == normalized_category_name)
                and (
                    normalized_source_channel is None
                    or record.source_channel == normalized_source_channel
                )
            )
        ]

        notes = []
        if self.settings.operational_postgres_dsn:
            notes.append(
                "Snapshots retornados do fallback em memoria porque o PostgreSQL operacional nao respondeu."
            )
        else:
            notes.append(
                "Snapshots retornados do fallback em memoria; configure PostgreSQL para persistencia duravel."
            )
        return TicketAnalyticsSnapshotListResult(
            snapshots=snapshots[:normalized_limit],
            storage_mode="memory",
            notes=notes,
        )

    async def _open_connection(self) -> Any | None:
        if not self.settings.operational_postgres_dsn or asyncpg is None:
            return None
        try:
            return await asyncpg.connect(self.settings.operational_postgres_dsn)
        except Exception:
            return None

    def _record_from_row(self, row: Any) -> TicketAnalyticsSnapshotRecord:
        attributes_json = self._decode_json(row["attributes_json"], default={})
        if not isinstance(attributes_json, dict):
            attributes_json = {}
        return TicketAnalyticsSnapshotRecord(
            ticket_id=row["ticket_id"],
            subject=row["subject"],
            description=row["description"],
            status=row["status"],
            priority=row["priority"],
            requester_glpi_user_id=row["requester_glpi_user_id"],
            assigned_glpi_user_id=row["assigned_glpi_user_id"],
            external_id=row["external_id"],
            request_type_id=row["request_type_id"],
            request_type_name=row["request_type_name"],
            category_id=row["category_id"],
            category_name=row["category_name"],
            asset_name=row["asset_name"],
            service_name=row["service_name"],
            source_channel=row["source_channel"],
            routed_to=row["routed_to"],
            correlation_event_count=int(row["correlation_event_count"] or 0),
            source_updated_at=row["source_updated_at"],
            source_audit_created_at=row["source_audit_created_at"],
            snapshot_created_at=row["snapshot_created_at"] or datetime.now(timezone.utc),
            snapshot_updated_at=row["snapshot_updated_at"] or datetime.now(timezone.utc),
            attributes_json=attributes_json,
        )

    def _normalize_record(self, record: TicketAnalyticsSnapshotRecord) -> TicketAnalyticsSnapshotRecord:
        now = datetime.now(timezone.utc)
        current_created_at = record.snapshot_created_at or now
        return TicketAnalyticsSnapshotRecord(
            ticket_id=self._optional_string(record.ticket_id) or "unknown-ticket",
            subject=self._optional_string(record.subject) or "Ticket sem assunto",
            description=self._optional_string(record.description),
            status=self._optional_string(record.status) or "unknown",
            priority=self._optional_string(record.priority),
            requester_glpi_user_id=record.requester_glpi_user_id,
            assigned_glpi_user_id=record.assigned_glpi_user_id,
            external_id=self._optional_string(record.external_id),
            request_type_id=record.request_type_id,
            request_type_name=self._optional_string(record.request_type_name),
            category_id=record.category_id,
            category_name=self._optional_string(record.category_name),
            asset_name=self._optional_string(record.asset_name),
            service_name=self._optional_string(record.service_name),
            source_channel=self._optional_string(record.source_channel),
            routed_to=self._optional_string(record.routed_to),
            correlation_event_count=max(0, int(record.correlation_event_count or 0)),
            source_updated_at=record.source_updated_at,
            source_audit_created_at=record.source_audit_created_at,
            snapshot_created_at=current_created_at,
            snapshot_updated_at=now,
            attributes_json=deepcopy(record.attributes_json) if isinstance(record.attributes_json, dict) else {},
        )

    def _clone_record(self, record: TicketAnalyticsSnapshotRecord) -> TicketAnalyticsSnapshotRecord:
        return TicketAnalyticsSnapshotRecord(
            ticket_id=record.ticket_id,
            subject=record.subject,
            description=record.description,
            status=record.status,
            priority=record.priority,
            requester_glpi_user_id=record.requester_glpi_user_id,
            assigned_glpi_user_id=record.assigned_glpi_user_id,
            external_id=record.external_id,
            request_type_id=record.request_type_id,
            request_type_name=record.request_type_name,
            category_id=record.category_id,
            category_name=record.category_name,
            asset_name=record.asset_name,
            service_name=record.service_name,
            source_channel=record.source_channel,
            routed_to=record.routed_to,
            correlation_event_count=record.correlation_event_count,
            source_updated_at=record.source_updated_at,
            source_audit_created_at=record.source_audit_created_at,
            snapshot_created_at=record.snapshot_created_at,
            snapshot_updated_at=record.snapshot_updated_at,
            attributes_json=deepcopy(record.attributes_json),
        )

    def _decode_json(self, value: Any, *, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return deepcopy(value)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return default

    def _optional_string(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None