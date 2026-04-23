from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.agent_runtime.knowledge import OperationalKnowledgeService
from app.agent_runtime.memory_store import AgentMemoryStore
from app.orchestration.helpdesk import HelpdeskOrchestrator
from app.schemas.helpdesk import CorrelationRequest
from app.services.automation import AutomationService
from app.services.glpi_analytics import GLPIAnalyticsSyncService
from app.services.ticket_analytics_store import TicketAnalyticsStore


class ReadOnlyInvestigationToolbox:
    def __init__(
        self,
        *,
        orchestrator: HelpdeskOrchestrator,
        analytics_store: TicketAnalyticsStore,
        analytics_sync_service: GLPIAnalyticsSyncService,
        automation_service: AutomationService,
        knowledge_service: OperationalKnowledgeService,
        memory_store: AgentMemoryStore,
    ) -> None:
        self.orchestrator = orchestrator
        self.analytics_store = analytics_store
        self.analytics_sync_service = analytics_sync_service
        self.automation_service = automation_service
        self.knowledge_service = knowledge_service
        self.memory_store = memory_store

    def build(self) -> dict[str, BaseTool]:
        orchestrator = self.orchestrator
        analytics_store = self.analytics_store
        analytics_sync_service = self.analytics_sync_service
        automation_service = self.automation_service
        knowledge_service = self.knowledge_service
        memory_store = self.memory_store

        @tool(
            "glpi_get_ticket_context",
            description=(
                "Carrega contexto read-only do ticket, sincroniza o snapshot analitico "
                "e devolve detalhes operacionais relevantes do GLPI."
            ),
        )
        async def glpi_get_ticket_context(ticket_id: str) -> dict[str, Any]:
            sync_summary = await analytics_sync_service.sync_ticket_snapshots(ticket_ids=[ticket_id])
            ticket = await orchestrator.glpi_client.get_ticket_analytics_details(ticket_id)
            snapshot = await analytics_store.get_snapshot(ticket_id)
            notes = list(ticket.notes)
            notes.extend(sync_summary.notes)
            if snapshot is None:
                notes.append(
                    "Snapshot analitico nao estava disponivel apos a sincronizacao do ticket."
                )
            return {
                "ticket": _json_safe(ticket),
                "snapshot": _json_safe(snapshot),
                "sync": _json_safe(sync_summary),
                "notes": _dedupe_preserve_order(notes),
            }

        @tool(
            "helpdesk_get_resolution_advice",
            description=(
                "Consulta o resumo de resolucao, acoes sugeridas e dicas operacionais "
                "para um ticket existente sem alterar o chamado."
            ),
        )
        async def helpdesk_get_resolution_advice(ticket_id: str) -> dict[str, Any]:
            advice = await orchestrator.advise_ticket_resolution(ticket_id)
            return advice.model_dump()

        @tool(
            "ops_list_ticket_audit_events",
            description=(
                "Lista eventos de auditoria do ticket no store operacional para recuperar "
                "contexto historico recente da execucao."
            ),
        )
        async def ops_list_ticket_audit_events(
            ticket_id: str,
            limit: int = 8,
        ) -> dict[str, Any]:
            result = await orchestrator.list_audit_events(
                ticket_id=ticket_id,
                limit=max(1, min(limit, 20)),
            )
            return result.model_dump()

        @tool(
            "zabbix_find_related_events",
            description=(
                "Consulta o Zabbix para buscar eventos correlacionados por ativo ou servico "
                "em modo somente leitura."
            ),
        )
        async def zabbix_find_related_events(
            asset_name: str | None = None,
            service_name: str | None = None,
            limit: int = 5,
        ) -> dict[str, Any]:
            correlation = await orchestrator.correlate(
                request=CorrelationRequest(
                    asset_name=asset_name,
                    service_name=service_name,
                    limit=max(1, min(limit, 10)),
                )
            )
            return correlation.model_dump()

        @tool(
            "automation_list_catalog",
            description=(
                "Lista o catalogo homologado de automacoes e suas politicas sem disparar execucao."
            ),
        )
        async def automation_list_catalog() -> dict[str, Any]:
            entries = []
            for entry in automation_service.get_catalog():
                entries.append(
                    {
                        "name": entry.name,
                        "description": entry.description,
                        "executor": entry.executor,
                        "risk_level": entry.risk_level,
                        "approval_mode": entry.approval_mode,
                        "requires_ticket_id": entry.requires_ticket_id,
                        "inject_ticket_context": entry.inject_ticket_context,
                    }
                )
            return {
                "entries": entries,
                "notes": [
                    "Catalogo homologado consultado sem criar jobs nem executar automacoes."
                ],
            }

        @tool(
            "knowledge_find_similar_incidents",
            description=(
                "Recupera incidentes historicos parecidos a partir do snapshot analitico "
                "e do contexto atual do ticket."
            ),
        )
        async def knowledge_find_similar_incidents(
            ticket_id: str | None = None,
            subject: str | None = None,
            category_name: str | None = None,
            asset_name: str | None = None,
            service_name: str | None = None,
            limit: int = 3,
        ) -> dict[str, Any]:
            hits, notes = await knowledge_service.find_similar_incidents(
                ticket_id=ticket_id,
                subject=subject,
                category_name=category_name,
                asset_name=asset_name,
                service_name=service_name,
                limit=limit,
            )
            return {
                "hits": _json_safe(hits),
                "notes": notes,
            }

        @tool(
            "knowledge_find_runbooks",
            description=(
                "Procura runbooks e documentos operacionais do repositorio com base no "
                "contexto do incidente atual."
            ),
        )
        async def knowledge_find_runbooks(
            subject: str | None = None,
            category_name: str | None = None,
            asset_name: str | None = None,
            service_name: str | None = None,
            limit: int = 3,
        ) -> dict[str, Any]:
            hits, notes = await knowledge_service.find_runbooks(
                subject=subject,
                category_name=category_name,
                asset_name=asset_name,
                service_name=service_name,
                limit=limit,
            )
            return {
                "hits": _json_safe(hits),
                "notes": notes,
            }

        @tool(
            "memory_find_operational_patterns",
            description=(
                "Recupera memorias operacionais duraveis do agente por classe de incidente, "
                "servico e contexto textual."
            ),
        )
        async def memory_find_operational_patterns(
            subject: str | None = None,
            category_name: str | None = None,
            asset_name: str | None = None,
            service_name: str | None = None,
            limit: int = 3,
        ) -> dict[str, Any]:
            result = await memory_store.search_memories(
                subject=subject,
                category_name=category_name,
                asset_name=asset_name,
                service_name=service_name,
                limit=limit,
            )
            return {
                "hits": _json_safe(result.hits),
                "storage_mode": result.storage_mode,
                "notes": result.notes,
            }

        return {
            "glpi_get_ticket_context": glpi_get_ticket_context,
            "helpdesk_get_resolution_advice": helpdesk_get_resolution_advice,
            "ops_list_ticket_audit_events": ops_list_ticket_audit_events,
            "zabbix_find_related_events": zabbix_find_related_events,
            "automation_list_catalog": automation_list_catalog,
            "knowledge_find_similar_incidents": knowledge_find_similar_incidents,
            "knowledge_find_runbooks": knowledge_find_runbooks,
            "memory_find_operational_patterns": memory_find_operational_patterns,
        }


def _json_safe(value: object) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped
