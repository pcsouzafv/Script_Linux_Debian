from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import re
import unicodedata
from typing import Any

from app.services.glpi import GLPIClient
from app.services.ticket_analytics_store import TicketAnalyticsSnapshotRecord, TicketAnalyticsStore


TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")
RESOLVED_STATUSES = {"solved", "closed"}


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    kind: str
    source: str
    title: str
    snippet: str
    reference: str
    score: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentIndexEntry:
    title: str
    reference: str
    content: str
    lines: tuple[str, ...]
    tokens: frozenset[str]


class OperationalKnowledgeService:
    def __init__(
        self,
        *,
        analytics_store: TicketAnalyticsStore,
        glpi_client: GLPIClient,
    ) -> None:
        self.analytics_store = analytics_store
        self.glpi_client = glpi_client
        self.repo_root = Path(__file__).resolve().parents[3]

    async def find_similar_incidents(
        self,
        *,
        ticket_id: str | None,
        subject: str | None,
        category_name: str | None,
        asset_name: str | None,
        service_name: str | None,
        limit: int = 3,
    ) -> tuple[list[KnowledgeHit], list[str]]:
        try:
            listing = await self.analytics_store.list_snapshots(limit=80)
        except Exception as exc:  # pragma: no cover
            return [], [f"Historico analitico indisponivel para busca de incidentes parecidos: {exc}"]

        requested_limit = max(1, min(limit, 5))
        ranked: list[tuple[int, TicketAnalyticsSnapshotRecord]] = []
        query_tokens = _tokenize(
            " ".join(part for part in (subject, asset_name, service_name, category_name) if part)
        )
        normalized_ticket_id = (ticket_id or "").strip()
        normalized_asset = _normalize_text(asset_name)
        normalized_service = _normalize_text(service_name)
        normalized_category = _normalize_text(category_name)

        for snapshot in listing.snapshots:
            if snapshot.ticket_id == normalized_ticket_id:
                continue

            score = 0
            snapshot_asset = _normalize_text(snapshot.asset_name)
            snapshot_service = _normalize_text(snapshot.service_name)
            snapshot_category = _normalize_text(snapshot.category_name)
            snapshot_text = " ".join(
                part
                for part in (
                    snapshot.subject,
                    snapshot.description or "",
                    snapshot.asset_name or "",
                    snapshot.service_name or "",
                    snapshot.category_name or "",
                )
                if part
            )
            snapshot_tokens = _tokenize(snapshot_text)
            overlap = len(query_tokens & snapshot_tokens)

            if normalized_service and snapshot_service == normalized_service:
                score += 6
            if normalized_asset and snapshot_asset == normalized_asset:
                score += 5
            if normalized_category and snapshot_category == normalized_category:
                score += 3
            if overlap:
                score += min(overlap, 4)
            if _normalize_text(snapshot.status) in RESOLVED_STATUSES:
                score += 2
            score += min(max(snapshot.correlation_event_count, 0), 2)

            if score <= 0:
                continue
            ranked.append((score, snapshot))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].source_updated_at or item[1].snapshot_updated_at,
                item[1].ticket_id,
            ),
            reverse=True,
        )

        hits: list[KnowledgeHit] = []
        for score, snapshot in ranked[:requested_limit]:
            resolution_context = await self.glpi_client.get_ticket_resolution_context(
                snapshot.ticket_id,
                limit=2,
            )
            resolution_excerpt = next(
                (entry.content for entry in resolution_context.entries if entry.content),
                None,
            )
            snippet_parts = [
                f"Ticket {snapshot.ticket_id} status={snapshot.status}",
                f"servico={snapshot.service_name or 'n/a'}",
                f"fila={snapshot.routed_to or 'n/a'}",
            ]
            if resolution_excerpt:
                snippet_parts.append(f"ultimo contexto: {resolution_excerpt[:180]}")
            hits.append(
                KnowledgeHit(
                    kind="similar_incident",
                    source="analytics-store",
                    title=f"Incidente semelhante {snapshot.ticket_id}",
                    snippet=" | ".join(snippet_parts),
                    reference=f"ticket:{snapshot.ticket_id}",
                    score=score,
                    metadata={
                        "ticket_id": snapshot.ticket_id,
                        "status": snapshot.status,
                        "priority": snapshot.priority,
                        "asset_name": snapshot.asset_name,
                        "service_name": snapshot.service_name,
                        "category_name": snapshot.category_name,
                        "routed_to": snapshot.routed_to,
                        "storage_mode": listing.storage_mode,
                        "resolution_mode": resolution_context.mode,
                    },
                )
            )

        notes: list[str] = []
        if hits:
            notes.append(
                f"Conhecimento enriquecido com {len(hits)} incidente(s) parecido(s) recuperado(s) do historico analitico."
            )
        return hits, notes

    async def find_runbooks(
        self,
        *,
        subject: str | None,
        category_name: str | None,
        asset_name: str | None,
        service_name: str | None,
        limit: int = 3,
    ) -> tuple[list[KnowledgeHit], list[str]]:
        documents = _load_document_index(self.repo_root)
        if not documents:
            return [], ["Nenhum documento operacional foi encontrado para servir como base de conhecimento."]

        requested_limit = max(1, min(limit, 5))
        query_text = " ".join(
            part for part in (subject, category_name, asset_name, service_name) if part
        )
        query_tokens = _tokenize(query_text)
        if not query_tokens:
            return [], []

        ranked: list[tuple[int, DocumentIndexEntry, str]] = []
        for document in documents:
            overlap = len(query_tokens & document.tokens)
            if overlap <= 0:
                continue

            score = overlap * 2
            normalized_reference = _normalize_text(document.reference)
            normalized_title = _normalize_text(document.title)
            normalized_service = _normalize_text(service_name)
            normalized_category = _normalize_text(category_name)

            if normalized_service and normalized_service in normalized_reference:
                score += 2
            if normalized_service and normalized_service in normalized_title:
                score += 2
            if normalized_category and normalized_category in normalized_title:
                score += 1

            snippet = _best_matching_snippet(document.lines, query_tokens) or document.title
            ranked.append((score, document, snippet))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].reference,
            ),
            reverse=True,
        )

        hits = [
            KnowledgeHit(
                kind="runbook",
                source="repo-docs",
                title=document.title,
                snippet=snippet[:220],
                reference=document.reference,
                score=score,
                metadata={
                    "query_tokens": sorted(query_tokens),
                },
            )
            for score, document, snippet in ranked[:requested_limit]
        ]
        notes: list[str] = []
        if hits:
            notes.append(
                f"Conhecimento enriquecido com {len(hits)} runbook(s) ou documento(s) operacional(is) do repositorio."
            )
        return hits, notes


@lru_cache(maxsize=1)
def _load_document_index(repo_root: Path) -> tuple[DocumentIndexEntry, ...]:
    candidate_paths = [
        *sorted((repo_root / "docs").glob("*.md")),
        repo_root / "backend" / "README.md",
    ]
    documents: list[DocumentIndexEntry] = []
    for path in candidate_paths:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        lines = tuple(line.strip() for line in content.splitlines() if line.strip())
        title = _extract_title(lines) or path.stem
        relative_path = path.relative_to(repo_root).as_posix()
        tokens = frozenset(_tokenize(f"{title}\n{content}\n{relative_path}"))
        documents.append(
            DocumentIndexEntry(
                title=title,
                reference=relative_path,
                content=content,
                lines=lines,
                tokens=tokens,
            )
        )
    return tuple(documents)


def _extract_title(lines: tuple[str, ...]) -> str | None:
    for line in lines:
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None


def _best_matching_snippet(lines: tuple[str, ...], query_tokens: set[str]) -> str | None:
    best_line: str | None = None
    best_score = 0
    for line in lines:
        line_tokens = _tokenize(line)
        overlap = len(query_tokens & line_tokens)
        if overlap > best_score:
            best_score = overlap
            best_line = line
    return best_line


def _tokenize(value: str) -> set[str]:
    normalized = _normalize_text(value)
    return {token for token in TOKEN_PATTERN.findall(normalized) if len(token) >= 3}


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(character for character in normalized if not unicodedata.combining(character))
    return ascii_only.lower().strip()
