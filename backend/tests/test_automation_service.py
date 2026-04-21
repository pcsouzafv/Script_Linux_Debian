import asyncio

import pytest

from app.core.config import Settings
from app.schemas.helpdesk import RequesterIdentity, TicketOpenRequest
from app.services.ansible_runner import AnsibleRunnerExecutionResult
from app.services.automation import AutomationService
from app.services.glpi import GLPIClient


class FakeAnsibleRunnerClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_playbook(
        self,
        *,
        project_slug: str,
        playbook_name: str,
        extravars: dict[str, object],
    ) -> AnsibleRunnerExecutionResult:
        self.calls.append(
            {
                "project_slug": project_slug,
                "playbook_name": playbook_name,
                "extravars": extravars,
            }
        )
        if project_slug == "ping-localhost":
            assert playbook_name == "ping_localhost.yml"
            assert extravars == {}
            return AnsibleRunnerExecutionResult(
                result_payload={
                    "executor": "ansible-runner",
                    "project_slug": project_slug,
                    "playbook_name": playbook_name,
                    "status": "successful",
                    "rc": 0,
                },
                notes=["playbook homologado executado via fake runner"],
            )

        if project_slug == "ticket-context-probe":
            assert playbook_name == "ticket_context_probe.yml"
            return AnsibleRunnerExecutionResult(
                result_payload={
                    "executor": "ansible-runner",
                    "project_slug": project_slug,
                    "playbook_name": playbook_name,
                    "status": "successful",
                    "rc": 0,
                    "artifact_data": {
                        "helpdesk_ticket_probe": {
                            "ticket_id": extravars["helpdesk_ticket_id"],
                            "context_label": extravars.get("context_label"),
                            "ticket_context": extravars["helpdesk_ticket_context"],
                        }
                    },
                },
                notes=["ticket context homologado executado via fake runner"],
            )

        raise AssertionError(f"Projeto runner inesperado: {project_slug}")


def test_automation_service_executes_ansible_catalog_entry() -> None:
    settings = Settings(_env_file=None)
    runner = FakeAnsibleRunnerClient()
    service = AutomationService(
        GLPIClient(settings),
        ansible_runner_client=runner,
    )

    result = asyncio.run(
        service.execute(
            automation_name="ansible.ping_localhost",
            ticket_id=None,
            parameters={},
        )
    )

    assert result.execution_status == "completed"
    assert result.result_payload["executor"] == "ansible-runner"
    assert runner.calls[0]["project_slug"] == "ping-localhost"
    assert runner.calls[0]["extravars"] == {}
    assert any("fake runner" in note for note in result.notes)


def test_automation_service_executes_ticket_bound_ansible_catalog_entry() -> None:
    settings = Settings(_env_file=None)
    glpi_client = GLPIClient(settings)
    runner = FakeAnsibleRunnerClient()
    service = AutomationService(
        glpi_client,
        ansible_runner_client=runner,
    )

    ticket = asyncio.run(
        glpi_client.create_ticket(
            TicketOpenRequest(
                subject="Probe runner com contexto",
                description="Chamado local apenas para validar contexto minimo do playbook.",
                requester=RequesterIdentity(
                    external_id="user-runner-probe",
                    display_name="Runner Probe",
                    phone_number="+5511988887777",
                ),
            )
        )
    )

    result = asyncio.run(
        service.execute(
            automation_name="ansible.ticket_context_probe",
            ticket_id=ticket.ticket_id,
            parameters={"context_label": "diagnostico-local"},
        )
    )

    ticket_snapshot = asyncio.run(glpi_client.get_ticket(ticket.ticket_id))
    expected_context = {
        "ticket_id": ticket_snapshot.ticket_id,
        "status": ticket_snapshot.status,
        "priority": ticket_snapshot.priority,
        "updated_at": ticket_snapshot.updated_at,
        "requester_glpi_user_id": ticket_snapshot.requester_glpi_user_id,
        "assigned_glpi_user_id": ticket_snapshot.assigned_glpi_user_id,
        "followup_count": ticket_snapshot.followup_count,
        "integration_mode": ticket_snapshot.mode,
    }
    assert runner.calls[-1]["project_slug"] == "ticket-context-probe"
    assert runner.calls[-1]["extravars"] == {
        "context_label": "diagnostico-local",
        "helpdesk_ticket_id": ticket.ticket_id,
        "helpdesk_automation_name": "ansible.ticket_context_probe",
        "helpdesk_ticket_context": expected_context,
    }
    assert result.execution_status == "completed"
    artifact = result.result_payload["artifact_data"]["helpdesk_ticket_probe"]
    assert artifact["ticket_id"] == ticket.ticket_id
    assert artifact["context_label"] == "diagnostico-local"
    assert artifact["ticket_context"]["integration_mode"] == "mock"
    assert any("ticket context homologado" in note for note in result.notes)


def test_automation_service_requires_ticket_id_for_ticket_context_probe() -> None:
    settings = Settings(_env_file=None)
    service = AutomationService(
        GLPIClient(settings),
        ansible_runner_client=FakeAnsibleRunnerClient(),
    )

    with pytest.raises(ValueError, match="exige ticket_id"):
        service.validate_request(
            automation_name="ansible.ticket_context_probe",
            ticket_id=None,
            reason="diagnostico",
            parameters={},
        )


def test_automation_service_rejects_parameters_for_ansible_catalog_entry() -> None:
    settings = Settings(_env_file=None)
    service = AutomationService(
        GLPIClient(settings),
        ansible_runner_client=FakeAnsibleRunnerClient(),
    )

    with pytest.raises(ValueError, match="nao aceita parameters"):
        service.validate_request(
            automation_name="ansible.ping_localhost",
            ticket_id=None,
            reason=None,
            parameters={"unexpected": True},
        )


def test_automation_service_rejects_unexpected_ticket_context_probe_parameter() -> None:
    settings = Settings(_env_file=None)
    service = AutomationService(
        GLPIClient(settings),
        ansible_runner_client=FakeAnsibleRunnerClient(),
    )

    with pytest.raises(ValueError, match="context_label"):
        service.validate_request(
            automation_name="ansible.ticket_context_probe",
            ticket_id="GLPI-LOCAL-123",
            reason=None,
            parameters={"unexpected": True},
        )