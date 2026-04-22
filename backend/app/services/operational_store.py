from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from uuid import uuid4

from app.core.config import Settings

try:
    import asyncpg
except ImportError:  # pragma: no cover - fallback remains available without the driver
    asyncpg = None


SAFE_SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_OPERATIONAL_SCHEMA = "helpdesk_platform"
MAX_MEMORY_AUDIT_EVENTS = 500
TERMINAL_JOB_STATUSES = ("completed", "dead-letter", "rejected", "cancelled")
APPROVAL_STATUSES = ("pending", "approved", "rejected")
EXECUTION_STATUSES = (
    "awaiting-approval",
    "queued",
    "running",
    "retry-scheduled",
    "completed",
    "dead-letter",
    "cancelled",
    "rejected",
)
APPROVAL_EXPIRATION_ACTOR = "system-approval-expiration"
APPROVAL_EXPIRATION_REASON_CODE = "approval_timeout_expired"
APPROVAL_EXPIRATION_REASON = (
    "Aprovacao expirada automaticamente por exceder a janela configurada."
)


@dataclass(slots=True)
class OperationalSessionRecord:
    phone_number: str
    requester_display_name: str | None = None
    flow_name: str = "user_catalog_intake"
    stage: str = "awaiting_catalog"
    selected_catalog_code: str | None = None
    transcript: list[str] = field(default_factory=list)
    ticket_options: list[dict[str, str | None]] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class AuditEventRecord:
    event_id: str
    event_type: str
    actor_external_id: str | None
    actor_role: str | None
    ticket_id: str | None
    source_channel: str
    status: str
    payload_json: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class AuditEventListResult:
    events: list[AuditEventRecord]
    storage_mode: str
    retention_days: int | None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OperationalSessionListResult:
    sessions: list[OperationalSessionRecord]
    storage_mode: str
    total_sessions: int
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobRequestRecord:
    job_id: str
    automation_name: str
    approval_status: str
    execution_status: str
    requested_by: str | None = None
    ticket_id: str | None = None
    payload_json: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class JobRequestListResult:
    jobs: list[JobRequestRecord]
    storage_mode: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobRequestSummaryResult:
    storage_mode: str
    total_jobs: int
    approval_status_counts: dict[str, int]
    execution_status_counts: dict[str, int]
    oldest_job_created_at: datetime | None = None
    oldest_pending_approval_started_at: datetime | None = None
    oldest_queued_job_created_at: datetime | None = None
    oldest_running_started_at: datetime | None = None
    oldest_retry_scheduled_at: datetime | None = None
    notes: list[str] = field(default_factory=list)


_MEMORY_SESSION_STATE: dict[str, OperationalSessionRecord] = {}
_MEMORY_AUDIT_EVENTS: list[AuditEventRecord] = []
_MEMORY_JOB_REQUESTS: dict[str, JobRequestRecord] = {}


def clear_memory_operational_state() -> None:
    _MEMORY_SESSION_STATE.clear()
    _MEMORY_AUDIT_EVENTS.clear()
    _MEMORY_JOB_REQUESTS.clear()


def get_memory_audit_events() -> list[AuditEventRecord]:
    return list(_MEMORY_AUDIT_EVENTS)


class OperationalStateStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.schema_name = settings.operational_postgres_schema or DEFAULT_OPERATIONAL_SCHEMA
        if not SAFE_SCHEMA_NAME.fullmatch(self.schema_name):
            raise ValueError(
                "HELPDESK_OPERATIONAL_POSTGRES_SCHEMA deve usar apenas letras, numeros e underscore."
            )

    async def load_session(self, phone_number: str) -> OperationalSessionRecord | None:
        normalized_phone = self._normalize_phone(phone_number)
        row = await self._fetch_session_row(normalized_phone)
        if row is not None:
            record = self._session_from_row(row)
            _MEMORY_SESSION_STATE[normalized_phone] = self._clone_session_record(record)
            return record

        cached = _MEMORY_SESSION_STATE.get(normalized_phone)
        if cached is None:
            return None
        return self._clone_session_record(cached)

    async def save_session(self, record: OperationalSessionRecord) -> None:
        normalized_record = self._normalize_session_record(record)
        _MEMORY_SESSION_STATE[normalized_record.phone_number] = self._clone_session_record(
            normalized_record
        )

        connection = await self._open_connection()
        if connection is not None:
            try:
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema_name}.session_state (
                        session_key,
                        flow_name,
                        stage,
                        requester_display_name,
                        state_json,
                        updated_at
                    ) VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                    ON CONFLICT (session_key) DO UPDATE SET
                        flow_name = EXCLUDED.flow_name,
                        stage = EXCLUDED.stage,
                        requester_display_name = EXCLUDED.requester_display_name,
                        state_json = EXCLUDED.state_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    normalized_record.phone_number,
                    normalized_record.flow_name,
                    normalized_record.stage,
                    normalized_record.requester_display_name,
                    json.dumps(
                        {
                            "selected_catalog_code": normalized_record.selected_catalog_code,
                            "transcript": normalized_record.transcript,
                            "ticket_options": normalized_record.ticket_options,
                        },
                        ensure_ascii=True,
                    ),
                    normalized_record.updated_at,
                )
            finally:
                await connection.close()

        await self.record_audit_event(
            event_type="session_state_saved",
            actor_external_id=normalized_record.phone_number,
            actor_role="requester",
            source_channel="whatsapp",
            status="completed",
            payload_json={
                "flow_name": normalized_record.flow_name,
                "stage": normalized_record.stage,
                "selected_catalog_code": normalized_record.selected_catalog_code,
                "ticket_options": len(normalized_record.ticket_options),
                "transcript_entries": len(normalized_record.transcript),
            },
        )

    async def list_sessions(
        self,
        *,
        limit: int = 20,
    ) -> OperationalSessionListResult:
        normalized_limit = max(1, min(limit, 100))

        connection = await self._open_connection()
        if connection is not None:
            try:
                rows = await connection.fetch(
                    f"""
                    SELECT
                        session_key,
                        flow_name,
                        stage,
                        requester_display_name,
                        state_json,
                        updated_at,
                        COUNT(*) OVER() AS total_rows
                    FROM {self.schema_name}.session_state
                    ORDER BY updated_at DESC
                    LIMIT $1
                    """,
                    normalized_limit,
                )
            finally:
                await connection.close()

            total_sessions = int(rows[0]["total_rows"]) if rows else 0
            return OperationalSessionListResult(
                sessions=[self._session_from_row(row) for row in rows],
                storage_mode="postgres",
                total_sessions=total_sessions,
            )

        sessions = [
            self._clone_session_record(record)
            for record in sorted(
                _MEMORY_SESSION_STATE.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        ]

        notes: list[str] = []
        if self.settings.operational_postgres_dsn:
            notes.append(
                "Consulta de sessoes retornada a partir do fallback em memoria porque o PostgreSQL operacional nao respondeu."
            )
        else:
            notes.append(
                "Consulta de sessoes retornada a partir do fallback em memoria; configure PostgreSQL para historico duravel."
            )

        return OperationalSessionListResult(
            sessions=sessions[:normalized_limit],
            storage_mode="memory",
            total_sessions=len(sessions),
            notes=notes,
        )

    async def delete_session(self, phone_number: str, reason: str | None = None) -> None:
        normalized_phone = self._normalize_phone(phone_number)
        removed_record = _MEMORY_SESSION_STATE.pop(normalized_phone, None)

        connection = await self._open_connection()
        if connection is not None:
            try:
                row = await connection.fetchrow(
                    f"""
                    DELETE FROM {self.schema_name}.session_state
                    WHERE session_key = $1
                    RETURNING
                        session_key,
                        flow_name,
                        stage,
                        requester_display_name,
                        state_json,
                        updated_at
                    """,
                    normalized_phone,
                )
            finally:
                await connection.close()

            if row is not None:
                removed_record = self._session_from_row(row)

        if removed_record is None:
            return

        await self.record_audit_event(
            event_type="session_state_cleared",
            actor_external_id=normalized_phone,
            actor_role="requester",
            source_channel="whatsapp",
            status="completed",
            payload_json={
                "flow_name": removed_record.flow_name,
                "stage": removed_record.stage,
                "reason": reason,
            },
        )

    async def record_audit_event(
        self,
        *,
        event_type: str,
        actor_external_id: str | None = None,
        actor_role: str | None = None,
        ticket_id: str | None = None,
        source_channel: str = "system",
        status: str = "completed",
        payload_json: dict[str, Any] | None = None,
    ) -> AuditEventRecord:
        event = AuditEventRecord(
            event_id=str(uuid4()),
            event_type=event_type,
            actor_external_id=actor_external_id,
            actor_role=actor_role,
            ticket_id=ticket_id,
            source_channel=source_channel,
            status=status,
            payload_json=self._sanitize_payload(payload_json or {}),
        )

        connection = await self._open_connection()
        if connection is not None:
            try:
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema_name}.audit_event (
                        event_id,
                        event_type,
                        actor_external_id,
                        actor_role,
                        ticket_id,
                        source_channel,
                        status,
                        payload_json,
                        created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
                    """,
                    event.event_id,
                    event.event_type,
                    event.actor_external_id,
                    event.actor_role,
                    event.ticket_id,
                    event.source_channel,
                    event.status,
                    json.dumps(event.payload_json, ensure_ascii=True),
                    event.created_at,
                )
            finally:
                await connection.close()

        _MEMORY_AUDIT_EVENTS.append(event)
        if len(_MEMORY_AUDIT_EVENTS) > MAX_MEMORY_AUDIT_EVENTS:
            del _MEMORY_AUDIT_EVENTS[:-MAX_MEMORY_AUDIT_EVENTS]
        await self.purge_expired_audit_events()
        return event

    async def list_audit_events(
        self,
        *,
        limit: int = 20,
        event_type: str | None = None,
        ticket_id: str | None = None,
        actor_external_id: str | None = None,
    ) -> AuditEventListResult:
        normalized_limit = max(1, min(limit, 100))
        normalized_event_type = self._optional_string(event_type)
        normalized_ticket_id = self._optional_string(ticket_id)
        normalized_actor_external_id = self._optional_string(actor_external_id)

        connection = await self._open_connection()
        if connection is not None:
            try:
                rows = await connection.fetch(
                    f"""
                    SELECT
                        event_id,
                        event_type,
                        actor_external_id,
                        actor_role,
                        ticket_id,
                        source_channel,
                        status,
                        payload_json,
                        created_at
                    FROM {self.schema_name}.audit_event
                    WHERE ($1::text IS NULL OR event_type = $1)
                      AND ($2::text IS NULL OR ticket_id = $2)
                      AND ($3::text IS NULL OR actor_external_id = $3)
                    ORDER BY created_at DESC
                    LIMIT $4
                    """,
                    normalized_event_type,
                    normalized_ticket_id,
                    normalized_actor_external_id,
                    normalized_limit,
                )
            finally:
                await connection.close()

            return AuditEventListResult(
                events=[self._audit_event_from_row(row) for row in rows],
                storage_mode="postgres",
                retention_days=self.settings.operational_audit_retention_days,
            )

        filtered_events = [
            event
            for event in reversed(_MEMORY_AUDIT_EVENTS)
            if self._matches_audit_filters(
                event,
                event_type=normalized_event_type,
                ticket_id=normalized_ticket_id,
                actor_external_id=normalized_actor_external_id,
            )
        ]
        notes: list[str] = []
        if self.settings.operational_postgres_dsn:
            notes.append(
                "Consulta retornada a partir do fallback em memoria porque o PostgreSQL operacional nao respondeu."
            )
        else:
            notes.append(
                "Consulta retornada a partir do fallback em memoria; configure PostgreSQL para auditoria duravel."
            )
        return AuditEventListResult(
            events=filtered_events[:normalized_limit],
            storage_mode="memory",
            retention_days=self.settings.operational_audit_retention_days,
            notes=notes,
        )

    async def create_job_request(
        self,
        *,
        requested_by: str | None,
        ticket_id: str | None,
        automation_name: str,
        payload_json: dict[str, Any] | None = None,
        approval_status: str = "approved",
        execution_status: str = "queued",
    ) -> JobRequestRecord:
        await self.purge_expired_job_requests()
        await self.expire_stale_pending_job_requests()
        initial_payload = self._initialize_job_payload(payload_json or {})
        record = JobRequestRecord(
            job_id=str(uuid4()),
            requested_by=self._optional_string(requested_by),
            ticket_id=self._optional_string(ticket_id),
            automation_name=self._normalize_automation_name(automation_name),
            approval_status=self._normalize_job_status(approval_status, default="approved"),
            execution_status=self._normalize_job_status(execution_status, default="queued"),
            payload_json=initial_payload,
            created_at=datetime.now(timezone.utc),
        )
        _MEMORY_JOB_REQUESTS[record.job_id] = self._clone_job_request(record)

        connection = await self._open_connection()
        if connection is not None:
            try:
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema_name}.job_request (
                        job_id,
                        created_at,
                        requested_by,
                        ticket_id,
                        automation_name,
                        approval_status,
                        execution_status,
                        payload_json
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    """,
                    record.job_id,
                    record.created_at,
                    record.requested_by,
                    record.ticket_id,
                    record.automation_name,
                    record.approval_status,
                    record.execution_status,
                    json.dumps(record.payload_json, ensure_ascii=True),
                )
            finally:
                await connection.close()

        return self._clone_job_request(record)

    async def get_job_request(self, job_id: str) -> JobRequestRecord | None:
        await self.purge_expired_job_requests()
        await self.expire_stale_pending_job_requests()
        normalized_job_id = self._normalize_job_id(job_id)
        row = await self._fetch_job_row(normalized_job_id)
        if row is not None:
            record = self._job_request_from_row(row)
            _MEMORY_JOB_REQUESTS[normalized_job_id] = self._clone_job_request(record)
            return record

        cached = _MEMORY_JOB_REQUESTS.get(normalized_job_id)
        if cached is None:
            return None
        return self._clone_job_request(cached)

    async def list_job_requests(
        self,
        *,
        limit: int = 20,
        automation_name: str | None = None,
        ticket_id: str | None = None,
        approval_status: str | None = None,
        execution_status: str | None = None,
    ) -> JobRequestListResult:
        normalized_limit = max(1, min(limit, 100))
        normalized_automation_name = self._optional_string(automation_name)
        if normalized_automation_name:
            normalized_automation_name = self._normalize_automation_name(
                normalized_automation_name
            )
        normalized_ticket_id = self._optional_string(ticket_id)
        normalized_approval_status = self._optional_string(approval_status)
        if normalized_approval_status:
            normalized_approval_status = self._normalize_job_status(
                normalized_approval_status,
                default=normalized_approval_status,
            )
        normalized_execution_status = self._optional_string(execution_status)
        if normalized_execution_status:
            normalized_execution_status = self._normalize_job_status(
                normalized_execution_status,
                default=normalized_execution_status,
            )

        await self.purge_expired_job_requests()
        await self.expire_stale_pending_job_requests()

        connection = await self._open_connection()
        if connection is not None:
            try:
                rows = await connection.fetch(
                    f"""
                    SELECT
                        job_id,
                        created_at,
                        requested_by,
                        ticket_id,
                        automation_name,
                        approval_status,
                        execution_status,
                        payload_json
                    FROM {self.schema_name}.job_request
                    WHERE ($1::text IS NULL OR automation_name = $1)
                      AND ($2::text IS NULL OR ticket_id = $2)
                                            AND ($3::text IS NULL OR approval_status = $3)
                                            AND ($4::text IS NULL OR execution_status = $4)
                    ORDER BY created_at DESC
                                        LIMIT $5
                    """,
                    normalized_automation_name,
                    normalized_ticket_id,
                                        normalized_approval_status,
                    normalized_execution_status,
                    normalized_limit,
                )
            finally:
                await connection.close()

            return JobRequestListResult(
                jobs=[self._job_request_from_row(row) for row in rows],
                storage_mode="postgres",
            )

        jobs = [
            job
            for job in sorted(
                _MEMORY_JOB_REQUESTS.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
            if self._matches_job_filters(
                job,
                automation_name=normalized_automation_name,
                ticket_id=normalized_ticket_id,
                approval_status=normalized_approval_status,
                execution_status=normalized_execution_status,
            )
        ]

        notes: list[str] = []
        if self.settings.operational_postgres_dsn:
            notes.append(
                "Consulta de jobs retornada a partir do fallback em memoria porque o PostgreSQL operacional nao respondeu."
            )
        else:
            notes.append(
                "Consulta de jobs retornada a partir do fallback em memoria; configure PostgreSQL para historico duravel."
            )

        return JobRequestListResult(
            jobs=[self._clone_job_request(job) for job in jobs[:normalized_limit]],
            storage_mode="memory",
            notes=notes,
        )

    async def summarize_job_requests(self) -> JobRequestSummaryResult:
        await self.purge_expired_job_requests()
        await self.expire_stale_pending_job_requests()

        connection = await self._open_connection()
        if connection is not None:
            try:
                row = await connection.fetchrow(
                    f"""
                    SELECT
                        COUNT(*) AS total_jobs,
                        COUNT(*) FILTER (WHERE approval_status = 'pending') AS approval_pending_count,
                        COUNT(*) FILTER (WHERE approval_status = 'approved') AS approval_approved_count,
                        COUNT(*) FILTER (WHERE approval_status = 'rejected') AS approval_rejected_count,
                        COUNT(*) FILTER (WHERE execution_status = 'awaiting-approval') AS execution_awaiting_approval_count,
                        COUNT(*) FILTER (WHERE execution_status = 'queued') AS execution_queued_count,
                        COUNT(*) FILTER (WHERE execution_status = 'running') AS execution_running_count,
                        COUNT(*) FILTER (WHERE execution_status = 'retry-scheduled') AS execution_retry_scheduled_count,
                        COUNT(*) FILTER (WHERE execution_status = 'completed') AS execution_completed_count,
                        COUNT(*) FILTER (WHERE execution_status = 'dead-letter') AS execution_dead_letter_count,
                        COUNT(*) FILTER (WHERE execution_status = 'cancelled') AS execution_cancelled_count,
                        COUNT(*) FILTER (WHERE execution_status = 'rejected') AS execution_rejected_count,
                        MIN(created_at) AS oldest_job_created_at,
                        MIN(
                            COALESCE(
                                NULLIF(payload_json->'approval'->>'updated_at', '')::timestamptz,
                                created_at
                            )
                        ) FILTER (WHERE approval_status = 'pending' AND execution_status = 'awaiting-approval')
                            AS oldest_pending_approval_started_at,
                        MIN(created_at) FILTER (WHERE execution_status = 'queued')
                            AS oldest_queued_job_created_at,
                        MIN(
                            COALESCE(
                                NULLIF(payload_json->'execution'->>'started_at', '')::timestamptz,
                                created_at
                            )
                        ) FILTER (WHERE execution_status = 'running')
                            AS oldest_running_started_at,
                        MIN(NULLIF(payload_json->'execution'->>'retry_scheduled_at', '')::timestamptz)
                            FILTER (WHERE execution_status = 'retry-scheduled')
                            AS oldest_retry_scheduled_at
                    FROM {self.schema_name}.job_request
                    """
                )
            finally:
                await connection.close()

            if row is None:
                row_data: dict[str, Any] = {}
            else:
                row_data = dict(row)

            return JobRequestSummaryResult(
                storage_mode="postgres",
                total_jobs=int(row_data.get("total_jobs") or 0),
                approval_status_counts={
                    "pending": int(row_data.get("approval_pending_count") or 0),
                    "approved": int(row_data.get("approval_approved_count") or 0),
                    "rejected": int(row_data.get("approval_rejected_count") or 0),
                },
                execution_status_counts={
                    "awaiting-approval": int(row_data.get("execution_awaiting_approval_count") or 0),
                    "queued": int(row_data.get("execution_queued_count") or 0),
                    "running": int(row_data.get("execution_running_count") or 0),
                    "retry-scheduled": int(row_data.get("execution_retry_scheduled_count") or 0),
                    "completed": int(row_data.get("execution_completed_count") or 0),
                    "dead-letter": int(row_data.get("execution_dead_letter_count") or 0),
                    "cancelled": int(row_data.get("execution_cancelled_count") or 0),
                    "rejected": int(row_data.get("execution_rejected_count") or 0),
                },
                oldest_job_created_at=row_data.get("oldest_job_created_at"),
                oldest_pending_approval_started_at=row_data.get(
                    "oldest_pending_approval_started_at"
                ),
                oldest_queued_job_created_at=row_data.get("oldest_queued_job_created_at"),
                oldest_running_started_at=row_data.get("oldest_running_started_at"),
                oldest_retry_scheduled_at=row_data.get("oldest_retry_scheduled_at"),
            )

        approval_status_counts = self._empty_job_status_counts(APPROVAL_STATUSES)
        execution_status_counts = self._empty_job_status_counts(EXECUTION_STATUSES)
        oldest_job_created_at: datetime | None = None
        oldest_pending_approval_started_at: datetime | None = None
        oldest_queued_job_created_at: datetime | None = None
        oldest_running_started_at: datetime | None = None
        oldest_retry_scheduled_at: datetime | None = None

        for job in _MEMORY_JOB_REQUESTS.values():
            approval_status_counts[job.approval_status] = (
                approval_status_counts.get(job.approval_status, 0) + 1
            )
            execution_status_counts[job.execution_status] = (
                execution_status_counts.get(job.execution_status, 0) + 1
            )
            oldest_job_created_at = self._min_datetime(oldest_job_created_at, job.created_at)

            if job.approval_status == "pending" and job.execution_status == "awaiting-approval":
                oldest_pending_approval_started_at = self._min_datetime(
                    oldest_pending_approval_started_at,
                    self._extract_job_pending_timestamp(job),
                )
            if job.execution_status == "queued":
                oldest_queued_job_created_at = self._min_datetime(
                    oldest_queued_job_created_at,
                    job.created_at,
                )
            if job.execution_status == "running":
                oldest_running_started_at = self._min_datetime(
                    oldest_running_started_at,
                    self._extract_running_started_at(job.payload_json) or job.created_at,
                )
            if job.execution_status == "retry-scheduled":
                oldest_retry_scheduled_at = self._min_datetime(
                    oldest_retry_scheduled_at,
                    self._extract_retry_scheduled_at(job.payload_json),
                )

        notes: list[str] = []
        if self.settings.operational_postgres_dsn:
            notes.append(
                "Resumo de jobs retornado a partir do fallback em memoria porque o PostgreSQL operacional nao respondeu."
            )
        else:
            notes.append(
                "Resumo de jobs retornado a partir do fallback em memoria; configure PostgreSQL para historico duravel."
            )

        return JobRequestSummaryResult(
            storage_mode="memory",
            total_jobs=len(_MEMORY_JOB_REQUESTS),
            approval_status_counts=approval_status_counts,
            execution_status_counts=execution_status_counts,
            oldest_job_created_at=oldest_job_created_at,
            oldest_pending_approval_started_at=oldest_pending_approval_started_at,
            oldest_queued_job_created_at=oldest_queued_job_created_at,
            oldest_running_started_at=oldest_running_started_at,
            oldest_retry_scheduled_at=oldest_retry_scheduled_at,
            notes=notes,
        )

    async def annotate_job_queue(
        self,
        job_id: str,
        *,
        queue_mode: str,
        queue_key: str,
        notes: list[str] | None = None,
        dead_letter: bool = False,
    ) -> JobRequestRecord | None:
        payload_patch = {
            "queue": {
                "mode": self._optional_string(queue_mode) or "memory",
                "queue_key": self._optional_string(queue_key) or "helpdesk:automation:jobs",
                "target": "dead-letter" if dead_letter else "primary",
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
                "notes": [str(note) for note in (notes or []) if str(note).strip()],
            }
        }
        return await self._update_job_request(
            job_id,
            payload_patch=payload_patch,
        )

    async def acquire_job_for_execution(
        self,
        job_id: str,
        *,
        worker_id: str,
        queue_mode: str,
        queue_key: str,
    ) -> JobRequestRecord | None:
        normalized_job_id = self._normalize_job_id(job_id)
        normalized_worker_id = self._optional_string(worker_id)
        normalized_queue_mode = self._optional_string(queue_mode) or "memory"
        normalized_queue_key = (
            self._optional_string(queue_key) or "helpdesk:automation:jobs"
        )

        connection = await self._open_connection()
        if connection is not None:
            try:
                async with connection.transaction():
                    row = await connection.fetchrow(
                        f"""
                        SELECT
                            job_id,
                            created_at,
                            requested_by,
                            ticket_id,
                            automation_name,
                            approval_status,
                            execution_status,
                            payload_json
                        FROM {self.schema_name}.job_request
                        WHERE job_id = $1
                        FOR UPDATE
                        """,
                        normalized_job_id,
                    )
                    if row is None:
                        return None

                    current = self._job_request_from_row(row)
                    if current.approval_status != "approved" or current.execution_status != "queued":
                        return None

                    attempt_count = self._extract_attempt_count(current.payload_json) + 1
                    max_attempts = self._extract_max_attempts(current.payload_json)
                    updated = JobRequestRecord(
                        job_id=current.job_id,
                        created_at=current.created_at,
                        requested_by=current.requested_by,
                        ticket_id=current.ticket_id,
                        automation_name=current.automation_name,
                        approval_status=current.approval_status,
                        execution_status="running",
                        payload_json=self._merge_payload_json(
                            current.payload_json,
                            {
                                "queue": {
                                    "delivery_mode": normalized_queue_mode,
                                    "queue_key": normalized_queue_key,
                                    "dequeued_at": datetime.now(timezone.utc).isoformat(),
                                },
                                "execution": {
                                    "worker_id": normalized_worker_id,
                                    "started_at": datetime.now(timezone.utc).isoformat(),
                                    "finished_at": None,
                                    "attempt_count": attempt_count,
                                    "max_attempts": max_attempts,
                                },
                            },
                        ),
                    )
                    await connection.execute(
                        f"""
                        UPDATE {self.schema_name}.job_request
                        SET execution_status = $2,
                            payload_json = $3::jsonb
                        WHERE job_id = $1
                        """,
                        updated.job_id,
                        updated.execution_status,
                        json.dumps(updated.payload_json, ensure_ascii=True),
                    )
            finally:
                await connection.close()

            _MEMORY_JOB_REQUESTS[normalized_job_id] = self._clone_job_request(updated)
            return self._clone_job_request(updated)

        current = _MEMORY_JOB_REQUESTS.get(normalized_job_id)
        if (
            current is None
            or current.approval_status != "approved"
            or current.execution_status != "queued"
        ):
            return None

        attempt_count = self._extract_attempt_count(current.payload_json) + 1
        max_attempts = self._extract_max_attempts(current.payload_json)
        updated = JobRequestRecord(
            job_id=current.job_id,
            created_at=current.created_at,
            requested_by=current.requested_by,
            ticket_id=current.ticket_id,
            automation_name=current.automation_name,
            approval_status=current.approval_status,
            execution_status="running",
            payload_json=self._merge_payload_json(
                current.payload_json,
                {
                    "queue": {
                        "delivery_mode": normalized_queue_mode,
                        "queue_key": normalized_queue_key,
                        "dequeued_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "execution": {
                        "worker_id": normalized_worker_id,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "finished_at": None,
                        "attempt_count": attempt_count,
                        "max_attempts": max_attempts,
                    },
                },
            ),
        )
        _MEMORY_JOB_REQUESTS[normalized_job_id] = self._clone_job_request(updated)
        return self._clone_job_request(updated)

    async def mark_job_for_retry(
        self,
        job_id: str,
        *,
        worker_id: str,
        error_type: str,
        error_message: str,
        retry_scheduled_at: datetime,
        retry_delay_seconds: int,
    ) -> JobRequestRecord | None:
        return await self._update_job_request(
            job_id,
            execution_status="retry-scheduled",
            payload_patch={
                "execution": {
                    "worker_id": self._optional_string(worker_id),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "last_error": {
                        "error_type": self._optional_string(error_type),
                        "message": self._optional_string(error_message),
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "result": None,
                    "retry_scheduled_at": retry_scheduled_at.isoformat(),
                    "retry_delay_seconds": retry_delay_seconds,
                    "dead_lettered_at": None,
                }
            },
            only_when_execution_status_in={"running"},
        )

    async def acquire_due_retry_job(
        self,
        *,
        worker_id: str,
    ) -> JobRequestRecord | None:
        now = datetime.now(timezone.utc)
        normalized_worker_id = self._optional_string(worker_id)

        connection = await self._open_connection()
        if connection is not None:
            try:
                async with connection.transaction():
                    row = await connection.fetchrow(
                        f"""
                        SELECT
                            job_id,
                            created_at,
                            requested_by,
                            ticket_id,
                            automation_name,
                            approval_status,
                            execution_status,
                            payload_json
                        FROM {self.schema_name}.job_request
                        WHERE approval_status = 'approved'
                          AND execution_status = 'retry-scheduled'
                          AND COALESCE(
                                NULLIF(payload_json->'execution'->>'retry_scheduled_at', '')::timestamptz,
                                '-infinity'::timestamptz
                              ) <= $1
                        ORDER BY COALESCE(
                                    NULLIF(payload_json->'execution'->>'retry_scheduled_at', '')::timestamptz,
                                    '-infinity'::timestamptz
                                 ) ASC,
                                 created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """,
                        now,
                    )
                    if row is None:
                        return None

                    current = self._job_request_from_row(row)
                    attempt_count = self._extract_attempt_count(current.payload_json) + 1
                    max_attempts = self._extract_max_attempts(current.payload_json)
                    updated = JobRequestRecord(
                        job_id=current.job_id,
                        created_at=current.created_at,
                        requested_by=current.requested_by,
                        ticket_id=current.ticket_id,
                        automation_name=current.automation_name,
                        approval_status=current.approval_status,
                        execution_status="running",
                        payload_json=self._merge_payload_json(
                            current.payload_json,
                            {
                                "queue": {
                                    "delivery_mode": "scheduled-retry",
                                    "queue_key": "operational-store",
                                    "dequeued_at": now.isoformat(),
                                },
                                "execution": {
                                    "worker_id": normalized_worker_id,
                                    "started_at": now.isoformat(),
                                    "finished_at": None,
                                    "attempt_count": attempt_count,
                                    "max_attempts": max_attempts,
                                    "retry_scheduled_at": None,
                                    "retry_delay_seconds": None,
                                },
                            },
                        ),
                    )
                    await connection.execute(
                        f"""
                        UPDATE {self.schema_name}.job_request
                        SET execution_status = $2,
                            payload_json = $3::jsonb
                        WHERE job_id = $1
                        """,
                        updated.job_id,
                        updated.execution_status,
                        json.dumps(updated.payload_json, ensure_ascii=True),
                    )
            finally:
                await connection.close()

            _MEMORY_JOB_REQUESTS[updated.job_id] = self._clone_job_request(updated)
            return self._clone_job_request(updated)

        candidates = [
            job
            for job in _MEMORY_JOB_REQUESTS.values()
            if job.approval_status == "approved"
            and job.execution_status == "retry-scheduled"
            and (self._extract_retry_scheduled_at(job.payload_json) or datetime.max.replace(tzinfo=timezone.utc))
            <= now
        ]
        if not candidates:
            return None

        current = min(
            candidates,
            key=lambda item: (
                self._extract_retry_scheduled_at(item.payload_json)
                or datetime.max.replace(tzinfo=timezone.utc),
                item.created_at,
            ),
        )
        attempt_count = self._extract_attempt_count(current.payload_json) + 1
        max_attempts = self._extract_max_attempts(current.payload_json)
        updated = JobRequestRecord(
            job_id=current.job_id,
            created_at=current.created_at,
            requested_by=current.requested_by,
            ticket_id=current.ticket_id,
            automation_name=current.automation_name,
            approval_status=current.approval_status,
            execution_status="running",
            payload_json=self._merge_payload_json(
                current.payload_json,
                {
                    "queue": {
                        "delivery_mode": "scheduled-retry",
                        "queue_key": "operational-store",
                        "dequeued_at": now.isoformat(),
                    },
                    "execution": {
                        "worker_id": normalized_worker_id,
                        "started_at": now.isoformat(),
                        "finished_at": None,
                        "attempt_count": attempt_count,
                        "max_attempts": max_attempts,
                        "retry_scheduled_at": None,
                        "retry_delay_seconds": None,
                    },
                },
            ),
        )
        _MEMORY_JOB_REQUESTS[updated.job_id] = self._clone_job_request(updated)
        return self._clone_job_request(updated)

    async def mark_job_dead_letter(
        self,
        job_id: str,
        *,
        worker_id: str,
        queue_mode: str,
        queue_key: str,
        error_type: str,
        error_message: str,
    ) -> JobRequestRecord | None:
        return await self._update_job_request(
            job_id,
            execution_status="dead-letter",
            payload_patch={
                "queue": {
                    "mode": self._optional_string(queue_mode) or "memory",
                    "queue_key": self._optional_string(queue_key)
                    or "helpdesk:automation:jobs:dead-letter",
                    "target": "dead-letter",
                    "enqueued_at": datetime.now(timezone.utc).isoformat(),
                },
                "execution": {
                    "worker_id": self._optional_string(worker_id),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "last_error": {
                        "error_type": self._optional_string(error_type),
                        "message": self._optional_string(error_message),
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "result": None,
                    "retry_scheduled_at": None,
                    "retry_delay_seconds": None,
                    "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            only_when_execution_status_in={"running"},
        )

    async def finalize_job_execution(
        self,
        job_id: str,
        *,
        worker_id: str,
        execution_status: str,
        result_payload: dict[str, Any] | None = None,
        notes: list[str] | None = None,
    ) -> JobRequestRecord | None:
        payload_patch = {
            "execution": {
                "worker_id": self._optional_string(worker_id),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "result": self._sanitize_payload(result_payload or {}),
                "notes": [str(note) for note in (notes or []) if str(note).strip()],
                "last_error": None,
                "retry_scheduled_at": None,
                "retry_delay_seconds": None,
                "dead_lettered_at": None,
            }
        }
        return await self._update_job_request(
            job_id,
            execution_status=execution_status,
            payload_patch=payload_patch,
            only_when_execution_status_in={"running"},
        )

    async def approve_job_request(
        self,
        job_id: str,
        *,
        acted_by: str,
        reason_code: str | None = None,
        reason: str | None = None,
    ) -> JobRequestRecord | None:
        await self.expire_stale_pending_job_requests()
        updated_at = datetime.now(timezone.utc).isoformat()
        return await self._update_job_request(
            job_id,
            approval_status="approved",
            execution_status="queued",
            payload_patch={
                "approval": {
                    "status": "approved",
                    "acted_by": self._optional_string(acted_by),
                    "reason_code": self._optional_string(reason_code),
                    "reason": self._optional_string(reason),
                    "updated_at": updated_at,
                }
            },
            only_when_approval_status_in={"pending"},
            only_when_execution_status_in={"awaiting-approval"},
        )

    async def reject_job_request(
        self,
        job_id: str,
        *,
        acted_by: str,
        reason_code: str | None = None,
        reason: str | None = None,
    ) -> JobRequestRecord | None:
        await self.expire_stale_pending_job_requests()
        updated_at = datetime.now(timezone.utc).isoformat()
        return await self._update_job_request(
            job_id,
            approval_status="rejected",
            execution_status="rejected",
            payload_patch={
                "approval": {
                    "status": "rejected",
                    "acted_by": self._optional_string(acted_by),
                    "reason_code": self._optional_string(reason_code),
                    "reason": self._optional_string(reason),
                    "updated_at": updated_at,
                }
            },
            only_when_approval_status_in={"pending"},
            only_when_execution_status_in={"awaiting-approval"},
        )

    async def cancel_job_request(
        self,
        job_id: str,
        *,
        acted_by: str,
        reason_code: str | None = None,
        reason: str | None = None,
    ) -> JobRequestRecord | None:
        updated_at = datetime.now(timezone.utc).isoformat()
        return await self._update_job_request(
            job_id,
            execution_status="cancelled",
            payload_patch={
                "execution": {
                    "finished_at": updated_at,
                    "retry_scheduled_at": None,
                    "retry_delay_seconds": None,
                    "dead_lettered_at": None,
                    "cancellation": {
                        "acted_by": self._optional_string(acted_by),
                        "reason_code": self._optional_string(reason_code),
                        "reason": self._optional_string(reason),
                        "cancelled_at": updated_at,
                    },
                }
            },
            only_when_approval_status_in={"approved"},
            only_when_execution_status_in={"queued", "retry-scheduled"},
        )

    async def expire_stale_pending_job_requests(self) -> list[JobRequestRecord]:
        timeout_minutes = self.settings.automation_approval_timeout_minutes
        if timeout_minutes is None:
            return []

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=timeout_minutes)
        expired_jobs: list[JobRequestRecord] = []

        connection = await self._open_connection()
        if connection is not None:
            try:
                async with connection.transaction():
                    rows = await connection.fetch(
                        f"""
                        SELECT
                            job_id,
                            created_at,
                            requested_by,
                            ticket_id,
                            automation_name,
                            approval_status,
                            execution_status,
                            payload_json
                        FROM {self.schema_name}.job_request
                        WHERE approval_status = 'pending'
                          AND execution_status = 'awaiting-approval'
                          AND COALESCE(
                                NULLIF(payload_json->'approval'->>'updated_at', '')::timestamptz,
                                created_at
                              ) < $1
                        ORDER BY created_at ASC
                        FOR UPDATE
                        """,
                        cutoff,
                    )
                    for row in rows:
                        current = self._job_request_from_row(row)
                        updated = self._expire_pending_job_request(
                            current,
                            now=now,
                            timeout_minutes=timeout_minutes,
                        )
                        await connection.execute(
                            f"""
                            UPDATE {self.schema_name}.job_request
                            SET approval_status = $2,
                                execution_status = $3,
                                payload_json = $4::jsonb
                            WHERE job_id = $1
                            """,
                            updated.job_id,
                            updated.approval_status,
                            updated.execution_status,
                            json.dumps(updated.payload_json, ensure_ascii=True),
                        )
                        expired_jobs.append(updated)
            finally:
                await connection.close()

            for updated in expired_jobs:
                _MEMORY_JOB_REQUESTS[updated.job_id] = self._clone_job_request(updated)
        else:
            for job_id, current in list(_MEMORY_JOB_REQUESTS.items()):
                if current.approval_status != "pending":
                    continue
                if current.execution_status != "awaiting-approval":
                    continue
                if self._extract_job_pending_timestamp(current) >= cutoff:
                    continue

                updated = self._expire_pending_job_request(
                    current,
                    now=now,
                    timeout_minutes=timeout_minutes,
                )
                _MEMORY_JOB_REQUESTS[job_id] = self._clone_job_request(updated)
                expired_jobs.append(updated)

        for updated in expired_jobs:
            approval_section = updated.payload_json.get("approval")
            expiration_policy = (
                approval_section.get("expiration_policy")
                if isinstance(approval_section, dict)
                else {}
            )
            pending_since = (
                expiration_policy.get("pending_since")
                if isinstance(expiration_policy, dict)
                else None
            )
            expired_at = (
                expiration_policy.get("expired_at")
                if isinstance(expiration_policy, dict)
                else None
            )
            await self.record_audit_event(
                event_type="automation_job_approval_expired",
                actor_external_id=APPROVAL_EXPIRATION_ACTOR,
                actor_role="automation-policy",
                ticket_id=updated.ticket_id,
                source_channel="system",
                status=updated.execution_status,
                payload_json={
                    "automation_name": updated.automation_name,
                    "job_id": updated.job_id,
                    "approval_timeout_minutes": timeout_minutes,
                    "pending_since": pending_since,
                    "expired_at": expired_at,
                },
            )

        return [self._clone_job_request(job) for job in expired_jobs]

    async def purge_expired_audit_events(self) -> int:
        retention_days = self.settings.operational_audit_retention_days
        if retention_days is None:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        removed_from_memory = self._purge_memory_audit_events(cutoff)

        connection = await self._open_connection()
        if connection is None:
            return removed_from_memory

        try:
            removed_from_postgres = await connection.fetchval(
                f"""
                WITH deleted AS (
                    DELETE FROM {self.schema_name}.audit_event
                    WHERE created_at < $1
                    RETURNING 1
                )
                SELECT COUNT(*) FROM deleted
                """,
                cutoff,
            )
        finally:
            await connection.close()

        return int(removed_from_postgres or 0)

    async def purge_expired_job_requests(self) -> int:
        retention_days = self.settings.operational_job_retention_days
        if retention_days is None:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        removed_from_memory = self._purge_memory_job_requests(cutoff)

        connection = await self._open_connection()
        if connection is None:
            return removed_from_memory

        try:
            removed_from_postgres = await connection.fetchval(
                f"""
                WITH deleted AS (
                    DELETE FROM {self.schema_name}.job_request
                    WHERE execution_status = ANY($2::text[])
                      AND COALESCE(
                            NULLIF(payload_json->'execution'->>'dead_lettered_at', '')::timestamptz,
                            NULLIF(payload_json->'execution'->>'finished_at', '')::timestamptz,
                            NULLIF(payload_json->'approval'->>'updated_at', '')::timestamptz,
                            created_at
                          ) < $1
                    RETURNING 1
                )
                SELECT COUNT(*) FROM deleted
                """,
                cutoff,
                list(TERMINAL_JOB_STATUSES),
            )
        finally:
            await connection.close()

        return int(removed_from_postgres or 0)

    async def _fetch_session_row(self, phone_number: str) -> Any | None:
        connection = await self._open_connection()
        if connection is None:
            return None

        try:
            return await connection.fetchrow(
                f"""
                SELECT
                    session_key,
                    flow_name,
                    stage,
                    requester_display_name,
                    state_json,
                    updated_at
                FROM {self.schema_name}.session_state
                WHERE session_key = $1
                """,
                phone_number,
            )
        finally:
            await connection.close()

    async def _fetch_job_row(self, job_id: str) -> Any | None:
        connection = await self._open_connection()
        if connection is None:
            return None

        try:
            return await connection.fetchrow(
                f"""
                SELECT
                    job_id,
                    created_at,
                    requested_by,
                    ticket_id,
                    automation_name,
                    approval_status,
                    execution_status,
                    payload_json
                FROM {self.schema_name}.job_request
                WHERE job_id = $1
                """,
                job_id,
            )
        finally:
            await connection.close()

    async def _open_connection(self) -> Any | None:
        if not self.settings.operational_postgres_dsn or asyncpg is None:
            return None
        try:
            return await asyncpg.connect(self.settings.operational_postgres_dsn)
        except Exception:
            return None

    def _session_from_row(self, row: Any) -> OperationalSessionRecord:
        state_json = self._decode_json(row["state_json"], default={})
        if not isinstance(state_json, dict):
            state_json = {}
        return OperationalSessionRecord(
            phone_number=self._normalize_phone(row["session_key"]),
            requester_display_name=row["requester_display_name"],
            flow_name=row["flow_name"],
            stage=row["stage"],
            selected_catalog_code=self._optional_string(state_json.get("selected_catalog_code")),
            transcript=self._decode_json_list(state_json.get("transcript")),
            ticket_options=self._decode_ticket_options(state_json.get("ticket_options")),
            updated_at=row["updated_at"] or datetime.now(timezone.utc),
        )

    def _audit_event_from_row(self, row: Any) -> AuditEventRecord:
        payload_json = self._decode_json(row["payload_json"], default={})
        if not isinstance(payload_json, dict):
            payload_json = {}
        return AuditEventRecord(
            event_id=str(row["event_id"]),
            event_type=row["event_type"],
            actor_external_id=row["actor_external_id"],
            actor_role=row["actor_role"],
            ticket_id=row["ticket_id"],
            source_channel=row["source_channel"],
            status=row["status"],
            payload_json=self._sanitize_payload(payload_json),
            created_at=row["created_at"] or datetime.now(timezone.utc),
        )

    def _job_request_from_row(self, row: Any) -> JobRequestRecord:
        payload_json = self._decode_json(row["payload_json"], default={})
        if not isinstance(payload_json, dict):
            payload_json = {}
        return JobRequestRecord(
            job_id=str(row["job_id"]),
            created_at=row["created_at"] or datetime.now(timezone.utc),
            requested_by=self._optional_string(row["requested_by"]),
            ticket_id=self._optional_string(row["ticket_id"]),
            automation_name=self._normalize_automation_name(row["automation_name"]),
            approval_status=self._normalize_job_status(row["approval_status"], default="pending"),
            execution_status=self._normalize_job_status(
                row["execution_status"],
                default="queued",
            ),
            payload_json=self._sanitize_payload(payload_json),
        )

    def _normalize_session_record(self, record: OperationalSessionRecord) -> OperationalSessionRecord:
        return OperationalSessionRecord(
            phone_number=self._normalize_phone(record.phone_number),
            requester_display_name=record.requester_display_name,
            flow_name=record.flow_name,
            stage=record.stage,
            selected_catalog_code=record.selected_catalog_code,
            transcript=[str(entry) for entry in record.transcript],
            ticket_options=self._decode_ticket_options(record.ticket_options),
            updated_at=datetime.now(timezone.utc),
        )

    def _clone_session_record(self, record: OperationalSessionRecord) -> OperationalSessionRecord:
        return OperationalSessionRecord(
            phone_number=record.phone_number,
            requester_display_name=record.requester_display_name,
            flow_name=record.flow_name,
            stage=record.stage,
            selected_catalog_code=record.selected_catalog_code,
            transcript=list(record.transcript),
            ticket_options=deepcopy(record.ticket_options),
            updated_at=record.updated_at,
        )

    def _clone_job_request(self, record: JobRequestRecord) -> JobRequestRecord:
        return JobRequestRecord(
            job_id=record.job_id,
            created_at=record.created_at,
            requested_by=record.requested_by,
            ticket_id=record.ticket_id,
            automation_name=record.automation_name,
            approval_status=record.approval_status,
            execution_status=record.execution_status,
            payload_json=deepcopy(record.payload_json),
        )

    def _normalize_phone(self, phone_number: str) -> str:
        return "".join(character for character in str(phone_number) if character.isdigit())

    def _decode_json_list(self, value: Any) -> list[str]:
        decoded = self._decode_json(value, default=[])
        if not isinstance(decoded, list):
            return []
        return [str(entry) for entry in decoded]

    def _decode_ticket_options(self, value: Any) -> list[dict[str, str | None]]:
        decoded = self._decode_json(value, default=[])
        if not isinstance(decoded, list):
            return []

        normalized_options: list[dict[str, str | None]] = []
        for entry in decoded:
            if not isinstance(entry, dict):
                continue
            normalized_options.append(
                {
                    "ticket_id": self._optional_string(entry.get("ticket_id")),
                    "subject": self._optional_string(entry.get("subject")),
                    "status": self._optional_string(entry.get("status")),
                    "updated_at": self._optional_string(entry.get("updated_at")),
                }
            )
        return normalized_options

    def _decode_json(self, value: Any, *, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (list, dict)):
            return value
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
        if not normalized:
            return None
        return normalized

    def _normalize_job_id(self, job_id: str) -> str:
        normalized = self._optional_string(job_id)
        if not normalized:
            raise ValueError("job_id deve ser informado.")
        return normalized

    def _normalize_automation_name(self, automation_name: Any) -> str:
        normalized = self._optional_string(automation_name)
        if not normalized:
            raise ValueError("automation_name deve ser informado.")
        return normalized.lower()

    def _normalize_job_status(self, value: Any, *, default: str) -> str:
        normalized = self._optional_string(value)
        if not normalized:
            return default
        return normalized.lower()

    def _sanitize_payload(self, payload_json: dict[str, Any]) -> dict[str, Any]:
        return self._sanitize_mapping(payload_json, depth=0)

    def _sanitize_mapping(self, payload_json: dict[str, Any], *, depth: int) -> dict[str, Any]:
        if depth >= self.settings.operational_payload_max_depth:
            return {"__truncated__": "max-depth"}

        sanitized: dict[str, Any] = {}
        items = list(payload_json.items())
        max_keys = self.settings.operational_payload_max_object_keys
        for key, value in items[:max_keys]:
            sanitized[self._truncate_string(str(key))] = self._sanitize_payload_value(
                value,
                depth=depth + 1,
            )

        omitted_keys = len(items) - max_keys
        if omitted_keys > 0:
            sanitized["__truncated_keys__"] = omitted_keys
        return sanitized

    def _sanitize_payload_value(self, value: Any, *, depth: int) -> Any:
        if value is None or isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, str):
            return self._truncate_string(value)
        if isinstance(value, dict):
            return self._sanitize_mapping(value, depth=depth)
        if isinstance(value, list):
            return self._sanitize_list(value, depth=depth)
        return self._truncate_string(str(value))

    def _sanitize_list(self, values: list[Any], *, depth: int) -> list[Any]:
        if depth >= self.settings.operational_payload_max_depth:
            return ["[truncated: max-depth]"]

        max_items = self.settings.operational_payload_max_list_items
        sanitized = [
            self._sanitize_payload_value(item, depth=depth + 1)
            for item in values[:max_items]
        ]
        omitted_items = len(values) - max_items
        if omitted_items > 0:
            sanitized.append(f"[truncated {omitted_items} additional items]")
        return sanitized

    def _truncate_string(self, value: str) -> str:
        normalized = str(value)
        max_length = self.settings.operational_payload_max_string_length
        if len(normalized) <= max_length:
            return normalized

        suffix = f"... [truncated]"
        if len(suffix) >= max_length:
            return normalized[:max_length]

        prefix_length = max_length - len(suffix)
        return normalized[:prefix_length] + suffix

    def _initialize_job_payload(self, payload_json: dict[str, Any]) -> dict[str, Any]:
        return self._merge_payload_json(
            {
                "execution": {
                    "attempt_count": 0,
                    "max_attempts": self.settings.automation_worker_max_attempts,
                    "retry_scheduled_at": None,
                    "retry_delay_seconds": None,
                    "last_error": None,
                    "dead_lettered_at": None,
                }
            },
            self._sanitize_payload(payload_json),
        )

    def _extract_attempt_count(self, payload_json: dict[str, Any]) -> int:
        execution_section = payload_json.get("execution")
        if not isinstance(execution_section, dict):
            return 0

        attempt_count = execution_section.get("attempt_count")
        if isinstance(attempt_count, bool):
            return 0
        if isinstance(attempt_count, int):
            return max(attempt_count, 0)
        try:
            return max(int(str(attempt_count).strip()), 0)
        except (TypeError, ValueError):
            return 0

    def _extract_max_attempts(self, payload_json: dict[str, Any]) -> int:
        execution_section = payload_json.get("execution")
        if not isinstance(execution_section, dict):
            return self.settings.automation_worker_max_attempts

        max_attempts = execution_section.get("max_attempts")
        if isinstance(max_attempts, bool):
            return self.settings.automation_worker_max_attempts
        if isinstance(max_attempts, int):
            return max(max_attempts, 1)
        try:
            return max(int(str(max_attempts).strip()), 1)
        except (TypeError, ValueError):
            return self.settings.automation_worker_max_attempts

    def _extract_retry_scheduled_at(self, payload_json: dict[str, Any]) -> datetime | None:
        execution_section = payload_json.get("execution")
        if not isinstance(execution_section, dict):
            return None

        return self._extract_datetime_value(execution_section.get("retry_scheduled_at"))

    def _extract_running_started_at(self, payload_json: dict[str, Any]) -> datetime | None:
        execution_section = payload_json.get("execution")
        if not isinstance(execution_section, dict):
            return None

        return self._extract_datetime_value(execution_section.get("started_at"))

    def _empty_job_status_counts(self, statuses: tuple[str, ...]) -> dict[str, int]:
        return {status: 0 for status in statuses}

    def _min_datetime(
        self,
        current_value: datetime | None,
        candidate_value: datetime | None,
    ) -> datetime | None:
        if candidate_value is None:
            return current_value
        if current_value is None:
            return candidate_value
        return min(current_value, candidate_value)

    def _merge_payload_json(
        self,
        base_payload: dict[str, Any],
        patch_payload: dict[str, Any],
        *,
        depth: int = 0,
    ) -> dict[str, Any]:
        merged = self._sanitize_mapping(base_payload, depth=depth)
        for key, value in self._sanitize_mapping(patch_payload, depth=depth).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_payload_json(
                    merged[key],
                    value,
                    depth=depth + 1,
                )
                continue
            merged[key] = value
        return self._sanitize_mapping(merged, depth=depth)

    def _matches_audit_filters(
        self,
        event: AuditEventRecord,
        *,
        event_type: str | None,
        ticket_id: str | None,
        actor_external_id: str | None,
    ) -> bool:
        if event_type and event.event_type != event_type:
            return False
        if ticket_id and event.ticket_id != ticket_id:
            return False
        if actor_external_id and event.actor_external_id != actor_external_id:
            return False
        return True

    def _matches_job_filters(
        self,
        job: JobRequestRecord,
        *,
        automation_name: str | None,
        ticket_id: str | None,
        approval_status: str | None,
        execution_status: str | None,
    ) -> bool:
        if automation_name and job.automation_name != automation_name:
            return False
        if ticket_id and job.ticket_id != ticket_id:
            return False
        if approval_status and job.approval_status != approval_status:
            return False
        if execution_status and job.execution_status != execution_status:
            return False
        return True

    async def _update_job_request(
        self,
        job_id: str,
        *,
        approval_status: str | None = None,
        execution_status: str | None = None,
        payload_patch: dict[str, Any] | None = None,
        only_when_approval_status_in: set[str] | None = None,
        only_when_execution_status_in: set[str] | None = None,
    ) -> JobRequestRecord | None:
        normalized_job_id = self._normalize_job_id(job_id)
        normalized_approval_status = (
            self._normalize_job_status(approval_status, default="pending")
            if approval_status is not None
            else None
        )
        normalized_execution_status = (
            self._normalize_job_status(execution_status, default="queued")
            if execution_status is not None
            else None
        )
        normalized_allowed_approval_statuses = (
            {
                self._normalize_job_status(status, default="pending")
                for status in only_when_approval_status_in
            }
            if only_when_approval_status_in
            else None
        )
        normalized_allowed_statuses = (
            {
                self._normalize_job_status(status, default="queued")
                for status in only_when_execution_status_in
            }
            if only_when_execution_status_in
            else None
        )
        sanitized_patch = self._sanitize_payload(payload_patch or {})

        connection = await self._open_connection()
        if connection is not None:
            try:
                async with connection.transaction():
                    row = await connection.fetchrow(
                        f"""
                        SELECT
                            job_id,
                            created_at,
                            requested_by,
                            ticket_id,
                            automation_name,
                            approval_status,
                            execution_status,
                            payload_json
                        FROM {self.schema_name}.job_request
                        WHERE job_id = $1
                        FOR UPDATE
                        """,
                        normalized_job_id,
                    )
                    if row is None:
                        return None

                    current = self._job_request_from_row(row)
                    if (
                        normalized_allowed_approval_statuses is not None
                        and current.approval_status not in normalized_allowed_approval_statuses
                    ):
                        return None
                    if (
                        normalized_allowed_statuses is not None
                        and current.execution_status not in normalized_allowed_statuses
                    ):
                        return None

                    updated = JobRequestRecord(
                        job_id=current.job_id,
                        created_at=current.created_at,
                        requested_by=current.requested_by,
                        ticket_id=current.ticket_id,
                        automation_name=current.automation_name,
                        approval_status=normalized_approval_status or current.approval_status,
                        execution_status=normalized_execution_status or current.execution_status,
                        payload_json=self._merge_payload_json(
                            current.payload_json,
                            sanitized_patch,
                        ),
                    )
                    await connection.execute(
                        f"""
                        UPDATE {self.schema_name}.job_request
                        SET approval_status = $2,
                            execution_status = $3,
                            payload_json = $4::jsonb
                        WHERE job_id = $1
                        """,
                        updated.job_id,
                        updated.approval_status,
                        updated.execution_status,
                        json.dumps(updated.payload_json, ensure_ascii=True),
                    )
            finally:
                await connection.close()

            _MEMORY_JOB_REQUESTS[normalized_job_id] = self._clone_job_request(updated)
            return self._clone_job_request(updated)

        current = _MEMORY_JOB_REQUESTS.get(normalized_job_id)
        if current is None:
            return None
        if (
            normalized_allowed_approval_statuses is not None
            and current.approval_status not in normalized_allowed_approval_statuses
        ):
            return None
        if (
            normalized_allowed_statuses is not None
            and current.execution_status not in normalized_allowed_statuses
        ):
            return None

        updated = JobRequestRecord(
            job_id=current.job_id,
            created_at=current.created_at,
            requested_by=current.requested_by,
            ticket_id=current.ticket_id,
            automation_name=current.automation_name,
            approval_status=normalized_approval_status or current.approval_status,
            execution_status=normalized_execution_status or current.execution_status,
            payload_json=self._merge_payload_json(current.payload_json, sanitized_patch),
        )
        _MEMORY_JOB_REQUESTS[normalized_job_id] = self._clone_job_request(updated)
        return self._clone_job_request(updated)

    def _purge_memory_audit_events(self, cutoff: datetime) -> int:
        original_count = len(_MEMORY_AUDIT_EVENTS)
        _MEMORY_AUDIT_EVENTS[:] = [
            event for event in _MEMORY_AUDIT_EVENTS if event.created_at >= cutoff
        ]
        return original_count - len(_MEMORY_AUDIT_EVENTS)

    def _purge_memory_job_requests(self, cutoff: datetime) -> int:
        expired_job_ids = [
            job_id
            for job_id, job in _MEMORY_JOB_REQUESTS.items()
            if job.execution_status in TERMINAL_JOB_STATUSES
            and self._extract_job_terminal_timestamp(job) < cutoff
        ]
        for job_id in expired_job_ids:
            _MEMORY_JOB_REQUESTS.pop(job_id, None)
        return len(expired_job_ids)

    def _extract_job_pending_timestamp(self, job: JobRequestRecord) -> datetime:
        approval_section = job.payload_json.get("approval")
        if isinstance(approval_section, dict):
            approval_updated_at = self._extract_datetime_value(
                approval_section.get("updated_at")
            )
            if approval_updated_at is not None:
                return approval_updated_at
        return job.created_at

    def _extract_job_terminal_timestamp(self, job: JobRequestRecord) -> datetime:
        execution_section = job.payload_json.get("execution")
        if isinstance(execution_section, dict):
            dead_lettered_at = self._extract_datetime_value(
                execution_section.get("dead_lettered_at")
            )
            if dead_lettered_at is not None:
                return dead_lettered_at

            finished_at = self._extract_datetime_value(execution_section.get("finished_at"))
            if finished_at is not None:
                return finished_at

        if job.execution_status == "rejected":
            approval_section = job.payload_json.get("approval")
            if isinstance(approval_section, dict):
                approval_updated_at = self._extract_datetime_value(
                    approval_section.get("updated_at")
                )
                if approval_updated_at is not None:
                    return approval_updated_at

        return job.created_at

    def _expire_pending_job_request(
        self,
        current: JobRequestRecord,
        *,
        now: datetime,
        timeout_minutes: int,
    ) -> JobRequestRecord:
        expired_at = now.isoformat()
        pending_since = self._extract_job_pending_timestamp(current).isoformat()
        return JobRequestRecord(
            job_id=current.job_id,
            created_at=current.created_at,
            requested_by=current.requested_by,
            ticket_id=current.ticket_id,
            automation_name=current.automation_name,
            approval_status="rejected",
            execution_status="rejected",
            payload_json=self._merge_payload_json(
                current.payload_json,
                {
                    "approval": {
                        "status": "rejected",
                        "acted_by": APPROVAL_EXPIRATION_ACTOR,
                        "reason_code": APPROVAL_EXPIRATION_REASON_CODE,
                        "reason": APPROVAL_EXPIRATION_REASON,
                        "updated_at": expired_at,
                        "expiration_policy": {
                            "pending_since": pending_since,
                            "timeout_minutes": timeout_minutes,
                            "expired_at": expired_at,
                        },
                    }
                },
            ),
        )

    def _extract_datetime_value(self, value: Any) -> datetime | None:
        normalized = self._optional_string(value)
        if not normalized:
            return None

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _sanitize_list_item(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return self._sanitize_payload(value)
        if isinstance(value, list):
            return [self._sanitize_list_item(item) for item in value]
        return str(value)