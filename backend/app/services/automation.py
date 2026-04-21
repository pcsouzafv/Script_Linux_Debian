from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.ansible_runner import AnsibleRunnerClient
from app.services.exceptions import IntegrationError
from app.services.glpi import GLPIClient


@dataclass(frozen=True, slots=True)
class AutomationCatalogEntry:
    name: str
    description: str
    executor: str = "internal"
    risk_level: str = "low"
    approval_mode: str = "auto"
    requires_ticket_id: bool = False
    inject_ticket_context: bool = False
    project_slug: str | None = None
    playbook_name: str | None = None


@dataclass(slots=True)
class AutomationValidatedRequest:
    automation_name: str
    ticket_id: str | None
    reason: str | None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AutomationExecutionResult:
    execution_status: str
    result_payload: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class AutomationService:
    _CATALOG: dict[str, AutomationCatalogEntry] = {
        "ansible.ping_localhost": AutomationCatalogEntry(
            name="ansible.ping_localhost",
            description=(
                "Executa playbook homologado de baixo risco via Ansible Runner para validar o runner local."
            ),
            executor="ansible-runner",
            risk_level="low",
            approval_mode="auto",
            requires_ticket_id=False,
            project_slug="ping-localhost",
            playbook_name="ping_localhost.yml",
        ),
        "ansible.ticket_context_probe": AutomationCatalogEntry(
            name="ansible.ticket_context_probe",
            description=(
                "Executa playbook homologado read-only via Ansible Runner, vinculado a um ticket e"
                " recebendo um snapshot operacional minimo do chamado."
            ),
            executor="ansible-runner",
            risk_level="moderate",
            approval_mode="manual",
            requires_ticket_id=True,
            inject_ticket_context=True,
            project_slug="ticket-context-probe",
            playbook_name="ticket_context_probe.yml",
        ),
        "glpi.ticket_snapshot": AutomationCatalogEntry(
            name="glpi.ticket_snapshot",
            description=(
                "Consulta somente leitura de metadados operacionais do ticket no GLPI."
            ),
            risk_level="moderate",
            approval_mode="manual",
            requires_ticket_id=True,
        ),
        "noop.healthcheck": AutomationCatalogEntry(
            name="noop.healthcheck",
            description="Valida o caminho fila -> worker -> persistencia sem efeito colateral.",
            risk_level="low",
            approval_mode="auto",
            requires_ticket_id=False,
        ),
    }

    def __init__(
        self,
        glpi_client: GLPIClient,
        *,
        ansible_runner_client: AnsibleRunnerClient | None = None,
    ) -> None:
        self.glpi_client = glpi_client
        self.ansible_runner_client = ansible_runner_client

    def get_catalog(self) -> list[AutomationCatalogEntry]:
        return [self._CATALOG[name] for name in sorted(self._CATALOG)]

    def get_catalog_entry(self, automation_name: str) -> AutomationCatalogEntry:
        normalized_name = self._normalize_automation_name(automation_name)
        entry = self._get_catalog_entry(normalized_name)
        if entry is None:
            allowed = ", ".join(sorted(self._CATALOG))
            raise ValueError(f"Automacao nao homologada. Use uma destas opcoes: {allowed}.")
        return entry

    def get_execution_policy(self, automation_name: str) -> dict[str, Any]:
        entry = self.get_catalog_entry(automation_name)
        approval_required = entry.approval_mode == "manual"
        return {
            "risk_level": entry.risk_level,
            "approval_mode": entry.approval_mode,
            "approval_required": approval_required,
        }

    def validate_request(
        self,
        *,
        automation_name: str,
        ticket_id: str | None,
        reason: str | None,
        parameters: dict[str, Any] | None,
    ) -> AutomationValidatedRequest:
        normalized_name = self._normalize_automation_name(automation_name)
        entry = self.get_catalog_entry(normalized_name)

        normalized_ticket_id = self._normalize_optional_text(ticket_id)
        if entry.requires_ticket_id and not normalized_ticket_id:
            raise ValueError(f"A automacao {normalized_name} exige ticket_id.")

        normalized_reason = self._normalize_optional_text(reason)
        sanitized_parameters = self._sanitize_parameters(normalized_name, parameters)

        return AutomationValidatedRequest(
            automation_name=normalized_name,
            ticket_id=normalized_ticket_id,
            reason=normalized_reason,
            parameters=sanitized_parameters,
        )

    async def execute(
        self,
        *,
        automation_name: str,
        ticket_id: str | None,
        parameters: dict[str, Any] | None,
    ) -> AutomationExecutionResult:
        validated = self.validate_request(
            automation_name=automation_name,
            ticket_id=ticket_id,
            reason=None,
            parameters=parameters,
        )
        entry = self._get_catalog_entry(validated.automation_name)
        if entry is None:
            raise IntegrationError("Catalogo de automacao inconsistente.")

        if validated.automation_name == "noop.healthcheck":
            processed_at = datetime.now(timezone.utc).isoformat()
            return AutomationExecutionResult(
                execution_status="completed",
                result_payload={
                    "processed_at": processed_at,
                    "probe_label": validated.parameters.get("probe_label"),
                    "result": "ok",
                },
                notes=["Automacao noop concluida para validar fila e worker."],
            )

        if entry.executor == "ansible-runner":
            if self.ansible_runner_client is None:
                raise IntegrationError(
                    "Cliente Ansible Runner indisponivel para esta automacao homologada."
                )
            if not entry.project_slug or not entry.playbook_name:
                raise IntegrationError(
                    f"Automacao {entry.name} nao possui projeto ou playbook homologado configurado."
                )

            runner_extravars = dict(validated.parameters)
            if validated.ticket_id:
                runner_extravars["helpdesk_ticket_id"] = validated.ticket_id
                runner_extravars["helpdesk_automation_name"] = validated.automation_name
            if entry.inject_ticket_context:
                if validated.ticket_id is None:
                    raise ValueError(f"A automacao {validated.automation_name} exige ticket_id.")
                ticket = await self.glpi_client.get_ticket(validated.ticket_id)
                runner_extravars["helpdesk_ticket_context"] = self._build_ticket_context(ticket)

            runner_result = await self.ansible_runner_client.run_playbook(
                project_slug=entry.project_slug,
                playbook_name=entry.playbook_name,
                extravars=runner_extravars,
            )
            return AutomationExecutionResult(
                execution_status="completed",
                result_payload=runner_result.result_payload,
                notes=runner_result.notes,
            )

        if validated.ticket_id is None:
            raise ValueError(f"A automacao {validated.automation_name} exige ticket_id.")

        ticket = await self.glpi_client.get_ticket(validated.ticket_id)
        return AutomationExecutionResult(
            execution_status="completed",
            result_payload={
                "ticket": {
                    "ticket_id": ticket.ticket_id,
                    "status": ticket.status,
                    "priority": ticket.priority,
                    "updated_at": ticket.updated_at,
                    "requester_glpi_user_id": ticket.requester_glpi_user_id,
                    "assigned_glpi_user_id": ticket.assigned_glpi_user_id,
                    "followup_count": ticket.followup_count,
                    "integration_mode": ticket.mode,
                }
            },
            notes=["Snapshot operacional do ticket coletado sem alterar o GLPI."],
        )

    def _sanitize_parameters(
        self,
        automation_name: str,
        parameters: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise ValueError("O campo parameters deve ser um objeto JSON.")

        if automation_name == "noop.healthcheck":
            unexpected_keys = set(parameters) - {"probe_label"}
            if unexpected_keys:
                raise ValueError(
                    "noop.healthcheck aceita apenas o parametro opcional probe_label."
                )

            probe_label = self._normalize_optional_text(parameters.get("probe_label"))
            if probe_label and len(probe_label) > 80:
                raise ValueError("probe_label deve ter no maximo 80 caracteres.")
            return {"probe_label": probe_label} if probe_label else {}

        if automation_name == "ansible.ticket_context_probe":
            unexpected_keys = set(parameters) - {"context_label"}
            if unexpected_keys:
                raise ValueError(
                    "ansible.ticket_context_probe aceita apenas o parametro opcional context_label."
                )

            context_label = self._normalize_optional_text(parameters.get("context_label"))
            if context_label and len(context_label) > 80:
                raise ValueError("context_label deve ter no maximo 80 caracteres.")
            return {"context_label": context_label} if context_label else {}

        if parameters:
            raise ValueError(f"A automacao {automation_name} nao aceita parameters.")
        return {}

    def _build_ticket_context(self, ticket: Any) -> dict[str, Any]:
        return {
            "ticket_id": ticket.ticket_id,
            "status": ticket.status,
            "priority": ticket.priority,
            "updated_at": ticket.updated_at,
            "requester_glpi_user_id": ticket.requester_glpi_user_id,
            "assigned_glpi_user_id": ticket.assigned_glpi_user_id,
            "followup_count": ticket.followup_count,
            "integration_mode": ticket.mode,
        }

    def _get_catalog_entry(self, automation_name: str) -> AutomationCatalogEntry | None:
        return self._CATALOG.get(automation_name)

    def _normalize_automation_name(self, automation_name: str) -> str:
        normalized = str(automation_name or "").strip().lower()
        if not normalized:
            raise ValueError("automation_name deve ser informado.")
        return normalized

    def _normalize_optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        return normalized