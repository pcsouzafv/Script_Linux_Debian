import asyncio
from pathlib import Path

import pytest

import app.services.ansible_runner as ansible_runner_service

from app.core.config import Settings
from app.services.ansible_runner import AnsibleRunnerClient, AnsibleRunnerExecutionResult
from app.services.exceptions import IntegrationError


def test_ansible_runner_uses_homologated_project_scaffold(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        automation_runner_base_dir="../infra/automation-runner/projects",
        automation_runner_timeout_seconds=120,
    )
    client = AnsibleRunnerClient(settings)

    def fake_run_playbook_sync(
        private_data_dir: Path,
        playbook_name: str,
        extravars: dict[str, object],
    ) -> AnsibleRunnerExecutionResult:
        assert private_data_dir.name == "ping-localhost"
        assert playbook_name == "ping_localhost.yml"
        assert extravars == {}
        return AnsibleRunnerExecutionResult(
            result_payload={"executor": "ansible-runner", "status": "successful", "rc": 0},
            notes=["runner fake executado"],
        )

    monkeypatch.setattr(client, "_run_playbook_sync", fake_run_playbook_sync)

    result = asyncio.run(
        client.run_playbook(
            project_slug="ping-localhost",
            playbook_name="ping_localhost.yml",
            extravars={},
        )
    )

    assert result.result_payload["executor"] == "ansible-runner"
    assert result.notes == ["runner fake executado"]


def test_ansible_runner_extracts_artifact_data_and_stdout_excerpt(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        automation_runner_base_dir="../infra/automation-runner/projects",
        automation_runner_timeout_seconds=120,
    )
    client = AnsibleRunnerClient(settings)

    class FakeRunnerModule:
        @staticmethod
        def run(**_: object) -> object:
            class FakeRunnerResult:
                status = "successful"
                rc = 0
                stats = {
                    "ok": {"localhost": 2},
                    "changed": {"localhost": 0},
                    "failures": {"localhost": 0},
                    "dark": {"localhost": 0},
                    "skipped": {"localhost": 0},
                    "ignored": {"localhost": 0},
                    "rescued": {"localhost": 0},
                }

                @property
                def events(self) -> object:
                    return iter(
                        [
                            {
                                "event": "playbook_on_task_start",
                                "stdout": "TASK [Emit sanitized ticket context] ***************************************",
                            },
                            {
                                "event": "runner_on_ok",
                                "stdout": "ok: [localhost] => {\"msg\": \"ticket_id=GLPI-LOCAL-123 label=diag\"}",
                            },
                            {
                                "event": "playbook_on_stats",
                                "stdout": "",
                                "event_data": {
                                    "artifact_data": {
                                        "helpdesk_ticket_probe": {
                                            "ticket_id": "GLPI-LOCAL-123",
                                            "context_label": "diag",
                                            "ticket_context": {
                                                "status": "queued-local",
                                                "followup_count": 0,
                                            },
                                        }
                                    }
                                },
                            },
                        ]
                    )

            return FakeRunnerResult()

    monkeypatch.setattr(ansible_runner_service, "ansible_runner_module", FakeRunnerModule())

    result = asyncio.run(
        client.run_playbook(
            project_slug="ping-localhost",
            playbook_name="ping_localhost.yml",
            extravars={"helpdesk_ticket_context": {"followup_count": 0}},
        )
    )

    assert result.result_payload["artifact_data"]["helpdesk_ticket_probe"]["ticket_id"] == "GLPI-LOCAL-123"
    assert (
        result.result_payload["artifact_data"]["helpdesk_ticket_probe"]["ticket_context"]["status"]
        == "queued-local"
    )
    assert any(
        "ticket_id=GLPI-LOCAL-123" in line
        for line in result.result_payload["stdout_excerpt"]
    )


def test_ansible_runner_rejects_unknown_project() -> None:
    settings = Settings(
        _env_file=None,
        automation_runner_base_dir="../infra/automation-runner/projects",
    )
    client = AnsibleRunnerClient(settings)

    with pytest.raises(IntegrationError, match="nao encontrado"):
        asyncio.run(
            client.run_playbook(
                project_slug="projeto-inexistente",
                playbook_name="ping_localhost.yml",
                extravars={},
            )
        )