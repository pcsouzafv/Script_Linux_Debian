from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langchain_core.tools import BaseTool

from app.agent_runtime.knowledge import OperationalKnowledgeService
from app.agent_runtime.memory_store import (
    AgentMemoryRecord,
    AgentMemoryStore,
    build_incident_memory_namespace,
)
from app.agent_runtime.policies import build_shadow_read_only_policy
from app.agent_runtime.state import AgentRuntimeEvidenceState, AgentRuntimeState
from app.agent_runtime.tools import ReadOnlyInvestigationToolbox
from app.core.config import Settings
from app.orchestration.helpdesk import HelpdeskOrchestrator
from app.schemas.helpdesk import (
    AgentInvestigationEvidenceResponse,
    AgentInvestigationKnowledgeHitResponse,
    AgentInvestigationMemoryHitResponse,
    AgentInvestigationPolicyResponse,
    AgentInvestigationRequest,
    AgentInvestigationResponse,
    CorrelatedEvent,
)
from app.services.glpi_analytics import GLPIAnalyticsSyncService


_MEMORY_CHECKPOINTER = InMemorySaver()
_POSTGRES_CHECKPOINTER_SETUP_LOCK = asyncio.Lock()
_POSTGRES_CHECKPOINTER_SETUP_READY: set[str] = set()


def clear_agent_runtime_memory() -> None:
    global _MEMORY_CHECKPOINTER

    _MEMORY_CHECKPOINTER = InMemorySaver()
    _POSTGRES_CHECKPOINTER_SETUP_READY.clear()


class AgentRuntimeService:
    def __init__(
        self,
        *,
        settings: Settings,
        orchestrator: HelpdeskOrchestrator,
    ) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self.memory_store = AgentMemoryStore(settings)

    async def investigate(
        self,
        payload: AgentInvestigationRequest,
    ) -> AgentInvestigationResponse:
        thread_id = self._resolve_thread_id(payload)
        available_tools = self._build_available_tools()

        async with self._open_checkpointer() as (checkpointer, checkpoint_mode):
            graph = self._compile_graph(checkpointer=checkpointer, tools=available_tools)
            config = {"configurable": {"thread_id": thread_id}}
            initial_state: AgentRuntimeState = {
                "mode": "shadow",
                "thread_id": thread_id,
                "requested_by": payload.requested_by or "agent-shadow",
                "checkpoint_mode": checkpoint_mode,
                "ticket_id": payload.ticket_id,
                "asset_name": payload.asset_name,
                "service_name": payload.service_name,
                "recommended_actions": [],
                "candidate_automations": [],
                "correlated_events": [],
                "knowledge_hits": [],
                "memory_hits": [],
                "used_tools": [],
                "available_tools": sorted(available_tools),
                "evidence": [],
                "notes": [],
            }

            try:
                final_state = await graph.ainvoke(initial_state, config=config)
            except Exception as exc:
                await self._record_failed_investigation(
                    payload=payload,
                    thread_id=thread_id,
                    checkpoint_mode=checkpoint_mode,
                    error_message=str(exc),
                )
                raise

            history_count = 0
            async for _snapshot in graph.aget_state_history(config):
                history_count += 1

        response_payload = dict(final_state)
        response_payload["checkpoint_history_count"] = history_count
        response_payload["checkpoint_mode"] = checkpoint_mode
        return self._build_response(response_payload)

    def _build_available_tools(self) -> dict[str, BaseTool]:
        analytics_sync_service = GLPIAnalyticsSyncService(
            self.orchestrator.glpi_client,
            self.orchestrator.operational_store,
            self.orchestrator.analytics_store,
        )
        toolbox = ReadOnlyInvestigationToolbox(
            orchestrator=self.orchestrator,
            analytics_store=self.orchestrator.analytics_store,
            analytics_sync_service=analytics_sync_service,
            automation_service=self.orchestrator.automation_service,
            knowledge_service=OperationalKnowledgeService(
                analytics_store=self.orchestrator.analytics_store,
                glpi_client=self.orchestrator.glpi_client,
            ),
            memory_store=self.memory_store,
        )
        return toolbox.build()

    def _compile_graph(
        self,
        *,
        checkpointer: object,
        tools: dict[str, BaseTool],
    ):
        graph = StateGraph(AgentRuntimeState)
        graph.add_node("load_context", self._node_load_context(tools))
        graph.add_node("collect_ticket_insight", self._node_collect_ticket_insight(tools))
        graph.add_node("correlate_monitoring", self._node_correlate_monitoring(tools))
        graph.add_node("collect_catalog", self._node_collect_catalog(tools))
        graph.add_node("retrieve_knowledge", self._node_retrieve_knowledge(tools))
        graph.add_node("retrieve_memory", self._node_retrieve_memory(tools))
        graph.add_node("apply_policy", self._node_apply_policy())
        graph.add_node("synthesize", self._node_synthesize())
        graph.add_node("persist_memory", self._node_persist_memory())
        graph.add_node("audit_investigation", self._node_audit_investigation())
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "collect_ticket_insight")
        graph.add_edge("collect_ticket_insight", "correlate_monitoring")
        graph.add_edge("correlate_monitoring", "collect_catalog")
        graph.add_edge("collect_catalog", "retrieve_knowledge")
        graph.add_edge("retrieve_knowledge", "retrieve_memory")
        graph.add_edge("retrieve_memory", "apply_policy")
        graph.add_edge("apply_policy", "synthesize")
        graph.add_edge("synthesize", "persist_memory")
        graph.add_edge("persist_memory", "audit_investigation")
        graph.add_edge("audit_investigation", END)
        return graph.compile(checkpointer=checkpointer)

    def _node_load_context(self, tools: dict[str, BaseTool]):
        async def load_context(state: AgentRuntimeState) -> AgentRuntimeState:
            ticket_id = state.get("ticket_id")
            if not ticket_id:
                return {
                    "notes": _merge_unique(
                        state.get("notes", []),
                        [
                            "Investigacao iniciada sem ticket; usando apenas o contexto informado na requisicao.",
                        ],
                    )
                }

            result = await tools["glpi_get_ticket_context"].ainvoke({"ticket_id": ticket_id})
            ticket = result.get("ticket") or {}
            snapshot = result.get("snapshot") or {}
            notes = _merge_unique(state.get("notes", []), result.get("notes", []))
            evidence = list(state.get("evidence", []))

            evidence.append(
                _evidence(
                    source="glpi",
                    kind="ticket",
                    title=f"Ticket {ticket_id}",
                    detail=ticket.get("subject"),
                    severity=ticket.get("priority"),
                    metadata={
                        "status": ticket.get("status"),
                        "priority": ticket.get("priority"),
                        "mode": ticket.get("mode"),
                    },
                )
            )
            if snapshot:
                evidence.append(
                    _evidence(
                        source="analytics-store",
                        kind="snapshot",
                        title="Snapshot analitico do ticket",
                        detail=(
                            f"Ativo: {snapshot.get('asset_name') or 'n/d'} | "
                            f"Servico: {snapshot.get('service_name') or 'n/d'}"
                        ),
                        severity=snapshot.get("priority"),
                        metadata={
                            "category_name": snapshot.get("category_name"),
                            "routed_to": snapshot.get("routed_to"),
                            "correlation_event_count": snapshot.get("correlation_event_count"),
                            "source_channel": snapshot.get("source_channel"),
                        },
                    )
                )

            return {
                "subject": ticket.get("subject"),
                "ticket_status": ticket.get("status"),
                "priority": ticket.get("priority"),
                "category_name": snapshot.get("category_name") or ticket.get("category_name"),
                "asset_name": state.get("asset_name") or snapshot.get("asset_name"),
                "service_name": state.get("service_name") or snapshot.get("service_name"),
                "routed_to": snapshot.get("routed_to"),
                "source_channel": snapshot.get("source_channel"),
                "used_tools": _append_unique(state.get("used_tools", []), "glpi_get_ticket_context"),
                "notes": notes,
                "evidence": evidence,
            }

        return load_context

    def _node_collect_ticket_insight(self, tools: dict[str, BaseTool]):
        async def collect_ticket_insight(state: AgentRuntimeState) -> AgentRuntimeState:
            ticket_id = state.get("ticket_id")
            if not ticket_id:
                return {}

            advice = await tools["helpdesk_get_resolution_advice"].ainvoke({"ticket_id": ticket_id})
            audit = await tools["ops_list_ticket_audit_events"].ainvoke(
                {"ticket_id": ticket_id, "limit": 8}
            )

            evidence = list(state.get("evidence", []))
            audit_events = audit.get("events", [])
            if advice.get("summary"):
                evidence.append(
                    _evidence(
                        source="helpdesk",
                        kind="resolution_advice",
                        title="Resumo tecnico do ticket",
                        detail=advice.get("summary"),
                        severity=advice.get("priority"),
                        metadata={
                            "integration_mode": advice.get("integration_mode"),
                            "recent_entries": len(advice.get("recent_entries", [])),
                        },
                    )
                )
            if audit_events:
                evidence.append(
                    _evidence(
                        source="operational-store",
                        kind="audit",
                        title="Eventos operacionais recentes",
                        detail=f"{len(audit_events)} evento(s) de auditoria vinculados ao ticket.",
                        metadata={
                            "event_types": [
                                event.get("event_type")
                                for event in audit_events[:5]
                                if isinstance(event, dict)
                            ]
                        },
                    )
                )

            return {
                "category_name": state.get("category_name") or advice.get("category_name"),
                "service_name": state.get("service_name") or advice.get("service_name"),
                "routed_to": state.get("routed_to") or advice.get("routed_to"),
                "summary": advice.get("summary") or state.get("summary"),
                "recommended_actions": _merge_unique(
                    state.get("recommended_actions", []),
                    advice.get("suggested_actions", []),
                ),
                "used_tools": _append_unique(
                    _append_unique(state.get("used_tools", []), "helpdesk_get_resolution_advice"),
                    "ops_list_ticket_audit_events",
                ),
                "notes": _merge_unique(
                    state.get("notes", []),
                    [*advice.get("notes", []), *audit.get("notes", [])],
                ),
                "evidence": evidence,
            }

        return collect_ticket_insight

    def _node_correlate_monitoring(self, tools: dict[str, BaseTool]):
        async def correlate_monitoring(state: AgentRuntimeState) -> AgentRuntimeState:
            asset_name = state.get("asset_name")
            service_name = state.get("service_name")
            if not (asset_name or service_name):
                return {
                    "notes": _merge_unique(
                        state.get("notes", []),
                        [
                            "Sem ativo ou servico suficientes para correlacionar monitoramento nesta investigacao.",
                        ],
                    )
                }

            correlation = await tools["zabbix_find_related_events"].ainvoke(
                {
                    "asset_name": asset_name,
                    "service_name": service_name,
                    "limit": 5,
                }
            )
            evidence = list(state.get("evidence", []))
            correlated_events = correlation.get("events", [])
            if correlated_events:
                evidence.append(
                    _evidence(
                        source="zabbix",
                        kind="monitoring",
                        title="Eventos correlacionados",
                        detail=f"{len(correlated_events)} evento(s) correlacionado(s) encontrados no Zabbix.",
                        metadata={
                            "event_ids": [
                                event.get("event_id")
                                for event in correlated_events
                                if isinstance(event, dict)
                            ],
                            "mode": correlation.get("mode"),
                        },
                    )
                )

            return {
                "correlated_events": correlated_events,
                "used_tools": _append_unique(
                    state.get("used_tools", []),
                    "zabbix_find_related_events",
                ),
                "notes": _merge_unique(state.get("notes", []), correlation.get("notes", [])),
                "evidence": evidence,
            }

        return correlate_monitoring

    def _node_collect_catalog(self, tools: dict[str, BaseTool]):
        async def collect_catalog(state: AgentRuntimeState) -> AgentRuntimeState:
            catalog = await tools["automation_list_catalog"].ainvoke({})
            candidates: list[str] = []
            for entry in catalog.get("entries", []):
                if not isinstance(entry, dict):
                    continue
                if entry.get("name") == "noop.healthcheck":
                    continue
                if entry.get("requires_ticket_id") and not state.get("ticket_id"):
                    continue
                if entry.get("name") == "ansible.ticket_context_probe" and not (
                    state.get("asset_name") or state.get("service_name")
                ):
                    continue
                name = str(entry.get("name") or "").strip()
                if name:
                    candidates.append(name)

            evidence = list(state.get("evidence", []))
            if candidates:
                evidence.append(
                    _evidence(
                        source="automation-catalog",
                        kind="automation_candidates",
                        title="Automacoes homologadas aplicaveis",
                        detail=f"{len(candidates)} automacao(oes) candidata(s) encontradas.",
                        metadata={"automation_names": candidates},
                    )
                )

            return {
                "candidate_automations": _merge_unique([], candidates),
                "used_tools": _append_unique(
                    state.get("used_tools", []),
                    "automation_list_catalog",
                ),
                "notes": _merge_unique(state.get("notes", []), catalog.get("notes", [])),
                "evidence": evidence,
            }

        return collect_catalog

    def _node_retrieve_knowledge(self, tools: dict[str, BaseTool]):
        async def retrieve_knowledge(state: AgentRuntimeState) -> AgentRuntimeState:
            similar_incidents = await tools["knowledge_find_similar_incidents"].ainvoke(
                {
                    "ticket_id": state.get("ticket_id"),
                    "subject": state.get("subject"),
                    "category_name": state.get("category_name"),
                    "asset_name": state.get("asset_name"),
                    "service_name": state.get("service_name"),
                    "limit": 3,
                }
            )
            runbooks = await tools["knowledge_find_runbooks"].ainvoke(
                {
                    "subject": state.get("subject"),
                    "category_name": state.get("category_name"),
                    "asset_name": state.get("asset_name"),
                    "service_name": state.get("service_name"),
                    "limit": 3,
                }
            )

            knowledge_hits = [
                hit
                for hit in [
                    *similar_incidents.get("hits", []),
                    *runbooks.get("hits", []),
                ]
                if isinstance(hit, dict)
            ]
            evidence = list(state.get("evidence", []))
            if knowledge_hits:
                evidence.append(
                    _evidence(
                        source="knowledge",
                        kind="retrieval",
                        title="Fontes operacionais recuperadas",
                        detail=f"{len(knowledge_hits)} referencia(s) de conhecimento anexada(s) a investigacao.",
                        metadata={
                            "kinds": [hit.get("kind") for hit in knowledge_hits[:6]],
                            "references": [hit.get("reference") for hit in knowledge_hits[:6]],
                        },
                    )
                )

            return {
                "knowledge_hits": knowledge_hits,
                "used_tools": _append_unique(
                    _append_unique(
                        state.get("used_tools", []),
                        "knowledge_find_similar_incidents",
                    ),
                    "knowledge_find_runbooks",
                ),
                "notes": _merge_unique(
                    state.get("notes", []),
                    [*similar_incidents.get("notes", []), *runbooks.get("notes", [])],
                ),
                "evidence": evidence,
            }

        return retrieve_knowledge

    def _node_retrieve_memory(self, tools: dict[str, BaseTool]):
        async def retrieve_memory(state: AgentRuntimeState) -> AgentRuntimeState:
            result = await tools["memory_find_operational_patterns"].ainvoke(
                {
                    "subject": state.get("subject"),
                    "category_name": state.get("category_name"),
                    "asset_name": state.get("asset_name"),
                    "service_name": state.get("service_name"),
                    "limit": 3,
                }
            )

            memory_hits = [hit for hit in result.get("hits", []) if isinstance(hit, dict)]
            evidence = list(state.get("evidence", []))
            if memory_hits:
                evidence.append(
                    _evidence(
                        source="agent-memory",
                        kind="memory",
                        title="Padroes operacionais anteriores",
                        detail=f"{len(memory_hits)} memoria(s) operacional(is) reaproveitada(s) pelo agente.",
                        metadata={
                            "namespace": [hit.get("namespace") for hit in memory_hits[:4]],
                            "storage_mode": result.get("storage_mode"),
                        },
                    )
                )

            return {
                "memory_hits": memory_hits,
                "used_tools": _append_unique(
                    state.get("used_tools", []),
                    "memory_find_operational_patterns",
                ),
                "notes": _merge_unique(state.get("notes", []), result.get("notes", [])),
                "evidence": evidence,
            }

        return retrieve_memory

    def _node_apply_policy(self):
        async def apply_policy(state: AgentRuntimeState) -> AgentRuntimeState:
            policy = build_shadow_read_only_policy(
                priority=state.get("priority"),
                ticket_status=state.get("ticket_status"),
                correlated_event_count=len(state.get("correlated_events", [])),
                candidate_automations=state.get("candidate_automations", []),
            )
            return {"policy": policy}

        return apply_policy

    def _node_synthesize(self):
        async def synthesize(state: AgentRuntimeState) -> AgentRuntimeState:
            correlated_events = state.get("correlated_events", [])
            summary = state.get("summary") or self._build_summary(state)
            if correlated_events:
                summary = (
                    f"{summary} Correlacao operacional encontrou {len(correlated_events)} "
                    "evento(s) relacionado(s) no Zabbix."
                )
            if state.get("knowledge_hits"):
                summary = (
                    f"{summary} O runtime tambem recuperou "
                    f"{len(state.get('knowledge_hits', []))} referencia(s) de conhecimento operacional."
                )
            if state.get("memory_hits"):
                summary = (
                    f"{summary} A memoria operacional do agente reaproveitou "
                    f"{len(state.get('memory_hits', []))} padrao(oes) de incidente."
                )

            recommended_actions = _merge_unique(
                state.get("recommended_actions", []),
                self._build_recommended_actions(state),
            )

            return {
                "summary": summary,
                "hypothesis": self._build_hypothesis(state),
                "recommended_actions": recommended_actions[:6],
                "notes": _merge_unique(state.get("notes", []), []),
                "evidence": state.get("evidence", [])[:10],
            }

        return synthesize

    def _node_persist_memory(self):
        async def persist_memory(state: AgentRuntimeState) -> AgentRuntimeState:
            if not state.get("summary"):
                return {}

            namespace = build_incident_memory_namespace(
                category_name=state.get("category_name"),
                service_name=state.get("service_name"),
            )
            memory_key = state.get("ticket_id") or state.get("thread_id") or namespace
            references = [
                {
                    "kind": hit.get("kind"),
                    "reference": hit.get("reference"),
                    "title": hit.get("title"),
                }
                for hit in state.get("knowledge_hits", [])
                if isinstance(hit, dict)
            ]
            await self.memory_store.upsert_memory(
                AgentMemoryRecord(
                    namespace=namespace,
                    memory_key=str(memory_key),
                    title=state.get("subject") or f"Memoria {namespace}",
                    summary=state.get("summary") or "Investigacao sem resumo.",
                    hypothesis=state.get("hypothesis"),
                    category_name=state.get("category_name"),
                    service_name=state.get("service_name"),
                    asset_name=state.get("asset_name"),
                    source_ticket_id=state.get("ticket_id"),
                    recommended_actions=state.get("recommended_actions", [])[:5],
                    references_json=references[:6],
                    attributes_json={
                        "correlated_event_count": len(state.get("correlated_events", [])),
                        "candidate_automations": state.get("candidate_automations", []),
                    },
                )
            )
            return {}

        return persist_memory

    def _node_audit_investigation(self):
        async def audit_investigation(state: AgentRuntimeState) -> AgentRuntimeState:
            try:
                await self.orchestrator.operational_store.record_audit_event(
                    event_type="agent_investigation_completed",
                    actor_external_id=state.get("requested_by"),
                    actor_role="agent-shadow",
                    ticket_id=state.get("ticket_id"),
                    source_channel="agent-runtime",
                    status="completed",
                    payload_json={
                        "thread_id": state.get("thread_id"),
                        "checkpoint_mode": state.get("checkpoint_mode"),
                        "mode": state.get("mode"),
                        "asset_name": state.get("asset_name"),
                        "service_name": state.get("service_name"),
                        "used_tools": state.get("used_tools", []),
                        "correlated_event_count": len(state.get("correlated_events", [])),
                        "knowledge_hit_count": len(state.get("knowledge_hits", [])),
                        "memory_hit_count": len(state.get("memory_hits", [])),
                        "candidate_automation_count": len(
                            state.get("candidate_automations", [])
                        ),
                        "policy_mode": (state.get("policy") or {}).get("mode"),
                    },
                )
            except Exception as exc:  # pragma: no cover
                return {
                    "notes": _merge_unique(
                        state.get("notes", []),
                        [
                            f"Falha ao registrar a auditoria da investigacao do agente: {exc}",
                        ],
                    )
                }
            return {}

        return audit_investigation

    async def _record_failed_investigation(
        self,
        *,
        payload: AgentInvestigationRequest,
        thread_id: str,
        checkpoint_mode: str,
        error_message: str,
    ) -> None:
        try:
            await self.orchestrator.operational_store.record_audit_event(
                event_type="agent_investigation_failed",
                actor_external_id=payload.requested_by,
                actor_role="agent-shadow",
                ticket_id=payload.ticket_id,
                source_channel="agent-runtime",
                status="failed",
                payload_json={
                    "thread_id": thread_id,
                    "checkpoint_mode": checkpoint_mode,
                    "asset_name": payload.asset_name,
                    "service_name": payload.service_name,
                    "error": error_message,
                },
            )
        except Exception:  # pragma: no cover
            return

    def _build_summary(self, state: AgentRuntimeState) -> str:
        ticket_id = state.get("ticket_id")
        if ticket_id and state.get("subject"):
            return f"Investigacao shadow concluida para o ticket {ticket_id}: {state['subject']}."
        if state.get("service_name") and state.get("asset_name"):
            return (
                f"Investigacao shadow concluida para o servico {state['service_name']} "
                f"no ativo {state['asset_name']}."
            )
        if state.get("service_name"):
            return f"Investigacao shadow concluida para o servico {state['service_name']}."
        if state.get("asset_name"):
            return f"Investigacao shadow concluida para o ativo {state['asset_name']}."
        return "Investigacao shadow concluida com o contexto operacional disponivel."

    def _build_hypothesis(self, state: AgentRuntimeState) -> str:
        correlated_event_count = len(state.get("correlated_events", []))
        service_name = state.get("service_name")
        asset_name = state.get("asset_name")
        ticket_status = state.get("ticket_status")

        if correlated_event_count > 0 and service_name:
            return (
                f"Ha indicios de incidente monitorado relacionado ao servico {service_name}, "
                f"com {correlated_event_count} evento(s) correlacionado(s)."
            )
        if correlated_event_count > 0 and asset_name:
            return (
                f"Ha indicios de incidente monitorado relacionado ao ativo {asset_name}, "
                f"com {correlated_event_count} evento(s) correlacionado(s)."
            )
        if ticket_status in {"new", "processing", "pending"} and (service_name or asset_name):
            return (
                "O chamado parece consistente com um incidente operacional, mas ainda sem "
                "confirmacao de monitoramento correlacionado."
            )
        return (
            "Nao houve evidencia suficiente para confirmar um incidente monitorado; "
            "seguir com investigacao humana controlada."
        )

    def _build_recommended_actions(self, state: AgentRuntimeState) -> list[str]:
        actions: list[str] = []
        if state.get("correlated_events"):
            actions.append(
                "Validar no Zabbix se os eventos correlacionados continuam ativos antes de qualquer remediacao."
            )
        for hit in state.get("knowledge_hits", [])[:2]:
            if not isinstance(hit, dict):
                continue
            if hit.get("kind") == "runbook":
                actions.append(
                    f"Consultar o documento {hit.get('title')} antes de qualquer mudanca operacional."
                )
            elif hit.get("kind") == "similar_incident":
                actions.append(
                    f"Comparar o caso atual com {hit.get('reference')} para reutilizar a experiencia operacional anterior."
                )
        for hit in state.get("memory_hits", [])[:2]:
            if not isinstance(hit, dict):
                continue
            actions.append(
                f"Revisar a memoria operacional {hit.get('memory_key')} no namespace {hit.get('namespace')} antes de decidir a proxima acao."
            )
        if state.get("ticket_id"):
            actions.append(
                "Registrar follow-up tecnico no ticket com as evidencias reunidas pelo runtime."
            )
        if "ansible.ticket_context_probe" in state.get("candidate_automations", []):
            actions.append(
                "Se precisar aprofundar a coleta, solicitar aprovacao para executar ansible.ticket_context_probe."
            )
        if state.get("candidate_automations"):
            actions.append(
                "Preferir automacoes homologadas do catalogo em vez de acao manual fora de trilha."
            )
        if not actions:
            actions.append(
                "Consolidar mais contexto operacional antes de decidir qualquer acao de mudanca."
            )
        return actions

    def _build_response(self, state: dict[str, Any]) -> AgentInvestigationResponse:
        policy = AgentInvestigationPolicyResponse.model_validate(state.get("policy") or {})
        evidence = [
            AgentInvestigationEvidenceResponse.model_validate(item)
            for item in state.get("evidence", [])
            if isinstance(item, dict)
        ]
        knowledge_hits = [
            AgentInvestigationKnowledgeHitResponse.model_validate(item)
            for item in state.get("knowledge_hits", [])
            if isinstance(item, dict)
        ]
        memory_hits = [
            AgentInvestigationMemoryHitResponse.model_validate(
                {
                    **item,
                    "updated_at": (
                        item.get("updated_at").isoformat()
                        if hasattr(item.get("updated_at"), "isoformat")
                        else str(item.get("updated_at") or "")
                    ),
                }
            )
            for item in state.get("memory_hits", [])
            if isinstance(item, dict)
        ]
        correlated_events = [
            CorrelatedEvent.model_validate(item)
            for item in state.get("correlated_events", [])
            if isinstance(item, dict)
        ]
        return AgentInvestigationResponse(
            mode=str(state.get("mode") or "shadow"),
            thread_id=str(state.get("thread_id") or ""),
            checkpoint_mode=str(state.get("checkpoint_mode") or "memory"),
            checkpoint_history_count=int(state.get("checkpoint_history_count") or 0),
            ticket_id=state.get("ticket_id"),
            subject=state.get("subject"),
            ticket_status=state.get("ticket_status"),
            priority=state.get("priority"),
            category_name=state.get("category_name"),
            asset_name=state.get("asset_name"),
            service_name=state.get("service_name"),
            routed_to=state.get("routed_to"),
            source_channel=state.get("source_channel"),
            summary=str(state.get("summary") or self._build_summary(state)),
            hypothesis=state.get("hypothesis"),
            recommended_actions=_merge_unique([], state.get("recommended_actions", [])),
            candidate_automations=_merge_unique([], state.get("candidate_automations", [])),
            correlated_events=correlated_events,
            knowledge_hits=knowledge_hits,
            memory_hits=memory_hits,
            used_tools=_merge_unique([], state.get("used_tools", [])),
            available_tools=_merge_unique([], state.get("available_tools", [])),
            policy=policy,
            evidence=evidence,
            notes=_merge_unique([], state.get("notes", [])),
        )

    def _resolve_thread_id(self, payload: AgentInvestigationRequest) -> str:
        if payload.thread_id:
            return payload.thread_id
        if payload.ticket_id:
            return f"agent:ticket:{payload.ticket_id}"
        scope_parts = [
            _slugify(payload.asset_name),
            _slugify(payload.service_name),
        ]
        scope = ":".join(part for part in scope_parts if part) or "scope"
        return f"agent:monitoring:{scope}"

    @asynccontextmanager
    async def _open_checkpointer(self) -> AsyncIterator[tuple[object, str]]:
        if not self.settings.operational_postgres_dsn:
            yield _MEMORY_CHECKPOINTER, "memory"
            return

        async with AsyncPostgresSaver.from_conn_string(
            self.settings.operational_postgres_dsn
        ) as saver:
            await self._ensure_postgres_checkpointer_setup(saver)
            yield saver, "postgres"

    async def _ensure_postgres_checkpointer_setup(
        self,
        saver: AsyncPostgresSaver,
    ) -> None:
        dsn = self.settings.operational_postgres_dsn or ""
        if dsn in _POSTGRES_CHECKPOINTER_SETUP_READY:
            return

        async with _POSTGRES_CHECKPOINTER_SETUP_LOCK:
            if dsn in _POSTGRES_CHECKPOINTER_SETUP_READY:
                return
            await saver.setup()
            _POSTGRES_CHECKPOINTER_SETUP_READY.add(dsn)


def _evidence(
    *,
    source: str,
    kind: str,
    title: str,
    detail: str | None = None,
    severity: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentRuntimeEvidenceState:
    return {
        "source": source,
        "kind": kind,
        "title": title,
        "detail": detail,
        "severity": severity,
        "metadata": metadata or {},
    }


def _append_unique(items: list[str], item: str) -> list[str]:
    return _merge_unique(items, [item])


def _merge_unique(current: list[str], incoming: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in [*current, *incoming]:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def _slugify(value: str | None) -> str:
    if not value:
        return ""
    normalized = "".join(
        character.lower()
        if character.isalnum()
        else "-"
        for character in value.strip()
    )
    compact = "-".join(part for part in normalized.split("-") if part)
    return compact[:80]
