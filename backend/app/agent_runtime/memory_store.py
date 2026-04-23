from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import unicodedata
from typing import Any

from app.core.config import Settings

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None


SAFE_SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_OPERATIONAL_SCHEMA = "helpdesk_platform"
TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")


@dataclass(slots=True)
class AgentMemoryRecord:
    namespace: str
    memory_key: str
    title: str
    summary: str
    hypothesis: str | None = None
    category_name: str | None = None
    service_name: str | None = None
    asset_name: str | None = None
    source_ticket_id: str | None = None
    recommended_actions: list[str] = field(default_factory=list)
    references_json: list[dict[str, Any]] = field(default_factory=list)
    usage_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    attributes_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentMemorySearchHit:
    namespace: str
    memory_key: str
    title: str
    summary: str
    hypothesis: str | None
    category_name: str | None
    service_name: str | None
    asset_name: str | None
    source_ticket_id: str | None
    recommended_actions: list[str] = field(default_factory=list)
    references_json: list[dict[str, Any]] = field(default_factory=list)
    usage_count: int = 0
    score: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class AgentMemorySearchResult:
    hits: list[AgentMemorySearchHit]
    storage_mode: str
    notes: list[str] = field(default_factory=list)


_MEMORY_AGENT_MEMORY: dict[tuple[str, str], AgentMemoryRecord] = {}
_POSTGRES_AGENT_MEMORY_SETUP_LOCK = asyncio.Lock()
_POSTGRES_AGENT_MEMORY_SETUP_READY: set[str] = set()


def clear_agent_memory_store() -> None:
    _MEMORY_AGENT_MEMORY.clear()
    _POSTGRES_AGENT_MEMORY_SETUP_READY.clear()


def build_incident_memory_namespace(
    *,
    category_name: str | None,
    service_name: str | None,
) -> str:
    normalized_category = _slugify(category_name) or "general"
    normalized_service = _slugify(service_name) or "general"
    return f"incident:{normalized_category}:{normalized_service}"


class AgentMemoryStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.schema_name = settings.operational_postgres_schema or DEFAULT_OPERATIONAL_SCHEMA
        if not SAFE_SCHEMA_NAME.fullmatch(self.schema_name):
            raise ValueError(
                "HELPDESK_OPERATIONAL_POSTGRES_SCHEMA deve usar apenas letras, numeros e underscore."
            )

    async def upsert_memory(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        normalized = self._normalize_record(record)
        _MEMORY_AGENT_MEMORY[(normalized.namespace, normalized.memory_key)] = self._clone_record(
            normalized
        )

        connection = await self._open_connection()
        if connection is not None:
            try:
                await self._ensure_storage_setup(connection)
                await connection.execute(
                    f"""
                    INSERT INTO {self.schema_name}.agent_memory (
                        namespace,
                        memory_key,
                        title,
                        summary,
                        hypothesis,
                        category_name,
                        service_name,
                        asset_name,
                        source_ticket_id,
                        recommended_actions_json,
                        references_json,
                        usage_count,
                        created_at,
                        updated_at,
                        last_used_at,
                        attributes_json
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9,
                        $10::jsonb, $11::jsonb, $12, $13, $14, $15, $16::jsonb
                    )
                    ON CONFLICT (namespace, memory_key) DO UPDATE SET
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        hypothesis = EXCLUDED.hypothesis,
                        category_name = EXCLUDED.category_name,
                        service_name = EXCLUDED.service_name,
                        asset_name = EXCLUDED.asset_name,
                        source_ticket_id = EXCLUDED.source_ticket_id,
                        recommended_actions_json = EXCLUDED.recommended_actions_json,
                        references_json = EXCLUDED.references_json,
                        usage_count = EXCLUDED.usage_count,
                        updated_at = EXCLUDED.updated_at,
                        last_used_at = EXCLUDED.last_used_at,
                        attributes_json = EXCLUDED.attributes_json
                    """,
                    normalized.namespace,
                    normalized.memory_key,
                    normalized.title,
                    normalized.summary,
                    normalized.hypothesis,
                    normalized.category_name,
                    normalized.service_name,
                    normalized.asset_name,
                    normalized.source_ticket_id,
                    json.dumps(normalized.recommended_actions, ensure_ascii=True),
                    json.dumps(normalized.references_json, ensure_ascii=True),
                    normalized.usage_count,
                    normalized.created_at,
                    normalized.updated_at,
                    normalized.last_used_at,
                    json.dumps(normalized.attributes_json, ensure_ascii=True),
                )
            finally:
                await connection.close()

        return self._clone_record(normalized)

    async def search_memories(
        self,
        *,
        category_name: str | None,
        service_name: str | None,
        asset_name: str | None,
        subject: str | None,
        limit: int = 3,
    ) -> AgentMemorySearchResult:
        requested_limit = max(1, min(limit, 5))
        namespaces = self._candidate_namespaces(
            category_name=category_name,
            service_name=service_name,
        )

        records: list[AgentMemoryRecord] = []
        connection = await self._open_connection()
        if connection is not None:
            try:
                await self._ensure_storage_setup(connection)
                rows = await connection.fetch(
                    f"""
                    SELECT *
                    FROM {self.schema_name}.agent_memory
                    WHERE namespace = ANY($1::text[])
                    ORDER BY updated_at DESC, namespace, memory_key
                    LIMIT 60
                    """,
                    list(namespaces),
                )
            finally:
                await connection.close()

            records = [self._record_from_row(row) for row in rows]
            for record in records:
                _MEMORY_AGENT_MEMORY[(record.namespace, record.memory_key)] = self._clone_record(record)
            return AgentMemorySearchResult(
                hits=self._rank_hits(
                    records=records,
                    category_name=category_name,
                    service_name=service_name,
                    asset_name=asset_name,
                    subject=subject,
                    namespaces=namespaces,
                    limit=requested_limit,
                ),
                storage_mode="postgres",
            )

        records = [
            self._clone_record(record)
            for record in _MEMORY_AGENT_MEMORY.values()
            if record.namespace in namespaces
        ]
        notes: list[str] = []
        if self.settings.operational_postgres_dsn:
            notes.append(
                "Memoria operacional do agente consultada a partir do fallback em memoria porque o PostgreSQL nao respondeu."
            )
        return AgentMemorySearchResult(
            hits=self._rank_hits(
                records=records,
                category_name=category_name,
                service_name=service_name,
                asset_name=asset_name,
                subject=subject,
                namespaces=namespaces,
                limit=requested_limit,
            ),
            storage_mode="memory",
            notes=notes,
        )

    async def _ensure_storage_setup(self, connection: Any) -> None:
        dsn = self.settings.operational_postgres_dsn or ""
        if dsn in _POSTGRES_AGENT_MEMORY_SETUP_READY:
            return

        async with _POSTGRES_AGENT_MEMORY_SETUP_LOCK:
            if dsn in _POSTGRES_AGENT_MEMORY_SETUP_READY:
                return
            await connection.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema_name}")
            await connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.schema_name}.agent_memory (
                    namespace TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    hypothesis TEXT NULL,
                    category_name TEXT NULL,
                    service_name TEXT NULL,
                    asset_name TEXT NULL,
                    source_ticket_id TEXT NULL,
                    recommended_actions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    references_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    last_used_at TIMESTAMPTZ NULL,
                    attributes_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    PRIMARY KEY (namespace, memory_key)
                )
                """
            )
            await connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS agent_memory_namespace_updated_idx
                ON {self.schema_name}.agent_memory (namespace, updated_at DESC)
                """
            )
            _POSTGRES_AGENT_MEMORY_SETUP_READY.add(dsn)

    async def _open_connection(self) -> Any | None:
        if not self.settings.operational_postgres_dsn or asyncpg is None:
            return None
        try:
            return await asyncpg.connect(self.settings.operational_postgres_dsn)
        except Exception:
            return None

    def _candidate_namespaces(
        self,
        *,
        category_name: str | None,
        service_name: str | None,
    ) -> tuple[str, ...]:
        exact_namespace = build_incident_memory_namespace(
            category_name=category_name,
            service_name=service_name,
        )
        category_namespace = build_incident_memory_namespace(
            category_name=category_name,
            service_name=None,
        )
        namespaces = [exact_namespace]
        if category_namespace != exact_namespace:
            namespaces.append(category_namespace)
        return tuple(dict.fromkeys(namespaces))

    def _rank_hits(
        self,
        *,
        records: list[AgentMemoryRecord],
        category_name: str | None,
        service_name: str | None,
        asset_name: str | None,
        subject: str | None,
        namespaces: tuple[str, ...],
        limit: int,
    ) -> list[AgentMemorySearchHit]:
        normalized_service = _normalize_text(service_name)
        normalized_asset = _normalize_text(asset_name)
        normalized_category = _normalize_text(category_name)
        query_tokens = _tokenize(
            " ".join(part for part in (subject, service_name, asset_name, category_name) if part)
        )

        ranked: list[tuple[int, AgentMemoryRecord]] = []
        for record in records:
            score = 0
            if record.namespace == namespaces[0]:
                score += 6
            elif record.namespace in namespaces[1:]:
                score += 3

            if normalized_service and _normalize_text(record.service_name) == normalized_service:
                score += 4
            if normalized_asset and _normalize_text(record.asset_name) == normalized_asset:
                score += 3
            if normalized_category and _normalize_text(record.category_name) == normalized_category:
                score += 2

            record_tokens = _tokenize(
                " ".join(
                    part
                    for part in (
                        record.title,
                        record.summary,
                        record.hypothesis or "",
                        record.service_name or "",
                        record.asset_name or "",
                        record.category_name or "",
                    )
                    if part
                )
            )
            overlap = len(query_tokens & record_tokens)
            score += min(overlap, 5)
            score += min(max(record.usage_count, 0), 3)
            if score <= 0:
                continue
            ranked.append((score, record))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].updated_at,
                item[1].memory_key,
            ),
            reverse=True,
        )

        return [
            AgentMemorySearchHit(
                namespace=record.namespace,
                memory_key=record.memory_key,
                title=record.title,
                summary=record.summary,
                hypothesis=record.hypothesis,
                category_name=record.category_name,
                service_name=record.service_name,
                asset_name=record.asset_name,
                source_ticket_id=record.source_ticket_id,
                recommended_actions=deepcopy(record.recommended_actions),
                references_json=deepcopy(record.references_json),
                usage_count=record.usage_count,
                score=score,
                updated_at=record.updated_at,
            )
            for score, record in ranked[:limit]
        ]

    def _record_from_row(self, row: Any) -> AgentMemoryRecord:
        recommended_actions = self._decode_json_list(row["recommended_actions_json"])
        references_json = self._decode_json_object_list(row["references_json"])
        attributes_json = self._decode_json_object(row["attributes_json"])
        return AgentMemoryRecord(
            namespace=row["namespace"],
            memory_key=row["memory_key"],
            title=row["title"],
            summary=row["summary"],
            hypothesis=row["hypothesis"],
            category_name=row["category_name"],
            service_name=row["service_name"],
            asset_name=row["asset_name"],
            source_ticket_id=row["source_ticket_id"],
            recommended_actions=recommended_actions,
            references_json=references_json,
            usage_count=max(0, int(row["usage_count"] or 0)),
            created_at=row["created_at"] or datetime.now(timezone.utc),
            updated_at=row["updated_at"] or datetime.now(timezone.utc),
            last_used_at=row["last_used_at"],
            attributes_json=attributes_json,
        )

    def _normalize_record(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        now = datetime.now(timezone.utc)
        created_at = record.created_at or now
        existing = _MEMORY_AGENT_MEMORY.get((record.namespace, record.memory_key))
        usage_count = max(0, int(record.usage_count or 0))
        if existing is not None:
            usage_count = max(existing.usage_count + 1, usage_count)
        return AgentMemoryRecord(
            namespace=self._optional_string(record.namespace) or "incident:general:general",
            memory_key=self._optional_string(record.memory_key) or "memory",
            title=self._optional_string(record.title) or "Memoria operacional",
            summary=self._optional_string(record.summary) or "Sem resumo operacional.",
            hypothesis=self._optional_string(record.hypothesis),
            category_name=self._optional_string(record.category_name),
            service_name=self._optional_string(record.service_name),
            asset_name=self._optional_string(record.asset_name),
            source_ticket_id=self._optional_string(record.source_ticket_id),
            recommended_actions=self._normalize_string_list(record.recommended_actions),
            references_json=self._normalize_object_list(record.references_json),
            usage_count=usage_count,
            created_at=created_at,
            updated_at=now,
            last_used_at=record.last_used_at,
            attributes_json=deepcopy(record.attributes_json) if isinstance(record.attributes_json, dict) else {},
        )

    def _clone_record(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        return AgentMemoryRecord(
            namespace=record.namespace,
            memory_key=record.memory_key,
            title=record.title,
            summary=record.summary,
            hypothesis=record.hypothesis,
            category_name=record.category_name,
            service_name=record.service_name,
            asset_name=record.asset_name,
            source_ticket_id=record.source_ticket_id,
            recommended_actions=deepcopy(record.recommended_actions),
            references_json=deepcopy(record.references_json),
            usage_count=record.usage_count,
            created_at=record.created_at,
            updated_at=record.updated_at,
            last_used_at=record.last_used_at,
            attributes_json=deepcopy(record.attributes_json),
        )

    def _decode_json_list(self, value: object) -> list[str]:
        decoded = self._decode_json(value, default=[])
        if not isinstance(decoded, list):
            return []
        return self._normalize_string_list(decoded)

    def _decode_json_object_list(self, value: object) -> list[dict[str, Any]]:
        decoded = self._decode_json(value, default=[])
        if not isinstance(decoded, list):
            return []
        return self._normalize_object_list(decoded)

    def _decode_json_object(self, value: object) -> dict[str, Any]:
        decoded = self._decode_json(value, default={})
        if not isinstance(decoded, dict):
            return {}
        return deepcopy(decoded)

    def _decode_json(self, value: object, *, default: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        if value is None:
            return default
        return value

    def _normalize_string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = self._optional_string(item)
            if text and text not in normalized:
                normalized.append(text)
        return normalized[:8]

    def _normalize_object_list(self, value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            normalized.append(deepcopy(item))
        return normalized[:8]

    def _optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


def _slugify(value: str | None) -> str:
    normalized = _normalize_text(value)
    compact = "-".join(part for part in re.split(r"[^a-z0-9]+", normalized) if part)
    return compact[:80]


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(character for character in normalized if not unicodedata.combining(character))
    return ascii_only.lower().strip()


def _tokenize(value: str) -> set[str]:
    normalized = _normalize_text(value)
    return {token for token in TOKEN_PATTERN.findall(normalized) if len(token) >= 3}
