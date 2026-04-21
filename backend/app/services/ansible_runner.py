from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from app.core.config import Settings
from app.services.exceptions import IntegrationError

try:
    import ansible_runner as ansible_runner_module
except ImportError:  # pragma: no cover - optional runtime dependency during development
    ansible_runner_module = None


SAFE_PROJECT_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SAFE_PLAYBOOK_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
ANSI_ESCAPE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


@dataclass(slots=True)
class AnsibleRunnerExecutionResult:
    result_payload: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class AnsibleRunnerClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.backend_dir = Path(__file__).resolve().parents[2]

    async def run_playbook(
        self,
        *,
        project_slug: str,
        playbook_name: str,
        extravars: dict[str, Any] | None = None,
    ) -> AnsibleRunnerExecutionResult:
        private_data_dir = self._resolve_private_data_dir(project_slug)
        self._validate_playbook_path(private_data_dir, playbook_name)
        sanitized_extravars = self._sanitize_extravars(extravars or {})
        return await asyncio.to_thread(
            self._run_playbook_sync,
            private_data_dir,
            playbook_name,
            sanitized_extravars,
        )

    def _run_playbook_sync(
        self,
        private_data_dir: Path,
        playbook_name: str,
        extravars: dict[str, Any],
    ) -> AnsibleRunnerExecutionResult:
        if ansible_runner_module is None:
            raise IntegrationError(
                "ansible-runner nao esta instalado no ambiente atual do backend."
            )

        runner_result = ansible_runner_module.run(
            private_data_dir=str(private_data_dir),
            playbook=playbook_name,
            extravars=extravars,
            quiet=True,
            rotate_artifacts=10,
            timeout=self.settings.automation_runner_timeout_seconds,
            json_mode=False,
        )

        runner_events = self._collect_runner_events(getattr(runner_result, "events", None))
        status = str(getattr(runner_result, "status", "unknown") or "unknown")
        rc = getattr(runner_result, "rc", None)
        normalized_rc = int(rc) if isinstance(rc, int) else -1
        if status != "successful" or normalized_rc != 0:
            stdout_excerpt = self._collect_stdout_excerpt(runner_events)
            failure_detail = f" detalhe={stdout_excerpt[-1]}" if stdout_excerpt else ""
            raise IntegrationError(
                "Playbook homologado falhou via Ansible Runner: "
                f"status={status} rc={normalized_rc}.{failure_detail}"
            )

        artifact_data = self._extract_artifact_data(runner_events)
        stdout_excerpt = self._collect_stdout_excerpt(runner_events)
        result_payload = {
            "executor": "ansible-runner",
            "project_slug": private_data_dir.name,
            "playbook_name": playbook_name,
            "status": status,
            "rc": normalized_rc,
            "stats": self._summarize_stats(getattr(runner_result, "stats", None)),
        }
        if artifact_data:
            result_payload["artifact_data"] = artifact_data
        if stdout_excerpt:
            result_payload["stdout_excerpt"] = stdout_excerpt

        return AnsibleRunnerExecutionResult(
            result_payload=result_payload,
            notes=["Playbook homologado executado via Ansible Runner."],
        )

    def _resolve_private_data_dir(self, project_slug: str) -> Path:
        normalized_slug = str(project_slug or "").strip().lower()
        if not SAFE_PROJECT_SLUG.fullmatch(normalized_slug):
            raise IntegrationError("Projeto de automacao homologado invalido.")

        base_dir = Path(self.settings.automation_runner_base_dir)
        if not base_dir.is_absolute():
            base_dir = (self.backend_dir / base_dir).resolve()
        else:
            base_dir = base_dir.resolve()

        private_data_dir = (base_dir / normalized_slug).resolve()
        if base_dir not in private_data_dir.parents and private_data_dir != base_dir:
            raise IntegrationError("Projeto de automacao homologado fora do diretorio permitido.")
        if not private_data_dir.exists() or not private_data_dir.is_dir():
            raise IntegrationError(
                f"Projeto homologado {normalized_slug} nao encontrado em {base_dir}."
            )
        return private_data_dir

    def _validate_playbook_path(self, private_data_dir: Path, playbook_name: str) -> None:
        normalized_playbook_name = str(playbook_name or "").strip()
        if not SAFE_PLAYBOOK_NAME.fullmatch(normalized_playbook_name):
            raise IntegrationError("Nome de playbook homologado invalido.")

        playbook_path = (private_data_dir / "project" / normalized_playbook_name).resolve()
        if private_data_dir not in playbook_path.parents:
            raise IntegrationError("Playbook homologado fora do projeto permitido.")
        if not playbook_path.exists() or not playbook_path.is_file():
            raise IntegrationError(
                f"Playbook homologado {normalized_playbook_name} nao encontrado no projeto {private_data_dir.name}."
            )

    def _sanitize_extravars(self, extravars: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in extravars.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            sanitized[normalized_key] = self._sanitize_value(value)
        return sanitized

    def _sanitize_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(nested_key): self._sanitize_value(nested_value)
                for nested_key, nested_value in value.items()
            }
        return str(value)

    def _collect_runner_events(self, events: Any) -> list[dict[str, Any]]:
        if events is None:
            return []

        collected: list[dict[str, Any]] = []
        try:
            for event in events:
                if isinstance(event, dict):
                    collected.append(event)
        except TypeError:
            return []
        return collected

    def _extract_artifact_data(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        for event in reversed(events):
            event_data = event.get("event_data")
            if not isinstance(event_data, dict):
                continue
            artifact_data = event_data.get("artifact_data")
            if isinstance(artifact_data, dict):
                return self._sanitize_extravars(artifact_data)
        return {}

    def _collect_stdout_excerpt(self, events: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for event in events:
            stdout = event.get("stdout")
            if not isinstance(stdout, str):
                continue
            for raw_line in stdout.splitlines():
                line = ANSI_ESCAPE.sub("", raw_line).strip()
                if line:
                    lines.append(line)
        return lines[-12:]

    def _summarize_stats(self, stats: Any) -> dict[str, int]:
        if not isinstance(stats, dict):
            return {}

        summary: dict[str, int] = {}
        for key in ("ok", "changed", "failures", "dark", "skipped", "ignored", "rescued"):
            value = stats.get(key)
            if isinstance(value, dict):
                summary[key] = sum(
                    item for item in value.values() if isinstance(item, int) and not isinstance(item, bool)
                )
                continue
            if isinstance(value, int) and not isinstance(value, bool):
                summary[key] = value
        return summary