from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict


class AgentRuntimePolicyState(TypedDict, total=False):
    mode: str
    can_read_data: bool
    can_execute_write_actions: bool
    approval_required_for_write: bool
    rationale: str
    notes: list[str]


class AgentRuntimeEvidenceState(TypedDict, total=False):
    source: str
    kind: str
    title: str
    detail: str | None
    severity: str | None
    metadata: dict[str, Any]


class AgentRuntimeState(TypedDict, total=False):
    mode: str
    thread_id: str
    requested_by: str
    checkpoint_mode: str
    checkpoint_history_count: int
    ticket_id: str | None
    subject: str | None
    ticket_status: str | None
    priority: str | None
    category_name: str | None
    asset_name: str | None
    service_name: str | None
    routed_to: str | None
    source_channel: str | None
    summary: str
    hypothesis: str | None
    recommended_actions: list[str]
    candidate_automations: list[str]
    correlated_events: list[dict[str, Any]]
    knowledge_hits: list[dict[str, Any]]
    memory_hits: list[dict[str, Any]]
    used_tools: list[str]
    available_tools: list[str]
    evidence: list[AgentRuntimeEvidenceState]
    policy: AgentRuntimePolicyState
    notes: list[str]
