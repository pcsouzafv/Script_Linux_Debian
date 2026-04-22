from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import shutil
import subprocess


@dataclass(slots=True)
class DockerContainerRecord:
    container_id: str
    name: str
    image: str
    status: str
    state: str
    application_name: str | None = None
    service_role: str = "service"
    health_status: str | None = None
    compose_project: str | None = None
    compose_service: str | None = None
    ports: str | None = None
    depends_on: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DockerApplicationRecord:
    application_name: str
    status: str
    total_containers: int
    running_count: int
    unhealthy_count: int
    application_services: list[str] = field(default_factory=list)
    support_services: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DockerRuntimeSnapshot:
    configured: bool
    status: str
    mode: str
    binary_path: str | None = None
    application_count: int = 0
    total_containers: int = 0
    running_count: int = 0
    exited_count: int = 0
    restarting_count: int = 0
    unhealthy_count: int = 0
    applications: list[DockerApplicationRecord] = field(default_factory=list)
    containers: list[DockerContainerRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class DockerRuntimeClient:
    async def get_runtime_snapshot(
        self,
        *,
        limit: int = 12,
    ) -> DockerRuntimeSnapshot:
        normalized_limit = max(1, min(limit, 100))
        return await asyncio.to_thread(self._get_runtime_snapshot_sync, normalized_limit)

    def _get_runtime_snapshot_sync(self, limit: int) -> DockerRuntimeSnapshot:
        docker_binary = shutil.which("docker")
        if not docker_binary:
            return DockerRuntimeSnapshot(
                configured=False,
                status="unavailable",
                mode="docker-cli",
                notes=["CLI do Docker nao foi encontrada no host atual."],
            )

        try:
            result = subprocess.run(
                [docker_binary, "ps", "-a", "--no-trunc", "--format", "{{json .}}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            notes = ["Falha ao consultar containers via CLI do Docker."]
            if detail:
                notes.append(detail)
            return DockerRuntimeSnapshot(
                configured=False,
                status="degraded",
                mode="docker-cli",
                binary_path=docker_binary,
                notes=notes,
            )
        except OSError as exc:
            return DockerRuntimeSnapshot(
                configured=False,
                status="degraded",
                mode="docker-cli",
                binary_path=docker_binary,
                notes=[f"Falha ao executar a CLI do Docker: {exc}"],
            )

        containers: list[DockerContainerRecord] = []
        for line in result.stdout.splitlines():
            normalized_line = line.strip()
            if not normalized_line:
                continue
            try:
                payload = json.loads(normalized_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            containers.append(self._container_from_payload(payload))

        application_records = self._build_application_records(containers)
        sorted_containers = sorted(containers, key=self._container_sort_key)
        return DockerRuntimeSnapshot(
            configured=True,
            status="configured",
            mode="docker-cli",
            binary_path=docker_binary,
            application_count=len(application_records),
            total_containers=len(containers),
            running_count=sum(1 for item in containers if item.state == "running"),
            exited_count=sum(1 for item in containers if item.state == "exited"),
            restarting_count=sum(1 for item in containers if item.state == "restarting"),
            unhealthy_count=sum(1 for item in containers if item.health_status == "unhealthy"),
            applications=application_records,
            containers=sorted_containers[:limit],
            notes=["Monitoramento Docker consultado via CLI local."],
        )

    def _container_from_payload(self, payload: dict[str, object]) -> DockerContainerRecord:
        status = str(payload.get("Status") or "unknown").strip()
        labels = self._parse_labels(payload.get("Labels"))
        compose_project = labels.get("com.docker.compose.project")
        compose_service = labels.get("com.docker.compose.service")
        depends_on = self._parse_compose_depends_on(labels.get("com.docker.compose.depends_on"))
        service_role = self._resolve_service_role(
            service_name=compose_service,
            image=str(payload.get("Image") or "unknown").strip(),
            container_name=str(payload.get("Names") or "unnamed").strip(),
        )
        return DockerContainerRecord(
            container_id=str(payload.get("ID") or "unknown").strip(),
            name=str(payload.get("Names") or "unnamed").strip(),
            image=str(payload.get("Image") or "unknown").strip(),
            status=status,
            state=self._resolve_state(status),
            application_name=compose_project,
            service_role=service_role,
            health_status=self._resolve_health_status(status),
            compose_project=compose_project,
            compose_service=compose_service,
            ports=self._optional_string(payload.get("Ports")),
            depends_on=depends_on,
        )

    def _build_application_records(
        self,
        containers: list[DockerContainerRecord],
    ) -> list[DockerApplicationRecord]:
        grouped: dict[str, list[DockerContainerRecord]] = {}
        for container in containers:
            key = container.compose_project or container.name
            grouped.setdefault(key, []).append(container)

        records: list[DockerApplicationRecord] = []
        for application_name, members in grouped.items():
            running_count = sum(1 for item in members if item.state == "running")
            unhealthy_count = sum(1 for item in members if item.health_status == "unhealthy")
            application_services = sorted(
                {
                    item.compose_service or item.name
                    for item in members
                    if item.service_role not in {"cache", "database", "queue", "ai-runtime"}
                }
            )
            support_services = sorted(
                {
                    self._describe_support_service(item)
                    for item in members
                    if item.service_role in {"cache", "database", "queue", "ai-runtime"}
                }
            )
            depends_on = sorted(
                {
                    dependency
                    for item in members
                    for dependency in item.depends_on
                }
            )

            if unhealthy_count > 0:
                status = "degraded"
            elif running_count < len(members):
                status = "partial"
            else:
                status = "running"

            notes: list[str] = []
            if any(item.service_role == "cache" for item in members):
                notes.append("Redis/cache detectado como apoio de desempenho do stack.")
            if any(item.service_role == "database" for item in members):
                notes.append("Banco de dados do stack monitorado no mesmo compose.")
            if depends_on:
                notes.append(f"Dependencias declaradas no compose: {', '.join(depends_on)}.")

            records.append(
                DockerApplicationRecord(
                    application_name=application_name,
                    status=status,
                    total_containers=len(members),
                    running_count=running_count,
                    unhealthy_count=unhealthy_count,
                    application_services=application_services,
                    support_services=support_services,
                    notes=notes,
                )
            )

        return sorted(records, key=self._application_sort_key)

    def _parse_labels(self, value: object) -> dict[str, str]:
        if not isinstance(value, str) or not value.strip():
            return {}

        labels: dict[str, str] = {}
        for raw_item in value.split(","):
            item = raw_item.strip()
            if not item:
                continue
            key, separator, label_value = item.partition("=")
            if not separator:
                labels[key] = ""
                continue
            labels[key.strip()] = label_value.strip()
        return labels

    def _parse_compose_depends_on(self, value: str | None) -> list[str]:
        if not value:
            return []

        dependencies: list[str] = []
        for raw_item in value.split(","):
            item = raw_item.strip()
            if not item:
                continue
            dependency_name, _, _ = item.partition(":")
            normalized = dependency_name.strip()
            if normalized:
                dependencies.append(normalized)
        return sorted(set(dependencies))

    def _resolve_service_role(
        self,
        *,
        service_name: str | None,
        image: str,
        container_name: str,
    ) -> str:
        primary_hint = " ".join(filter(None, [service_name, container_name])).lower()
        primary_tokens = {
            token
            for token in primary_hint.replace("_", "-").split("-")
            if token
        }
        image_hint = image.lower()

        if "redis" in primary_hint or "cache" in primary_hint or "redis" in image_hint:
            return "cache"
        if any(token in primary_hint for token in ("zabbix", "glpi", "portainer")):
            return "service"
        if primary_tokens & {"db", "database", "postgres", "mysql", "mariadb"}:
            return "database"
        if any(token in primary_hint for token in ("rabbitmq", "kafka", "queue", "broker")):
            return "queue"
        if "ollama" in primary_hint or "ollama" in image_hint:
            return "ai-runtime"
        if any(token in primary_hint for token in ("frontend", "web", "ui", "nginx")):
            return "frontend"
        if any(token in primary_hint for token in ("backend", "api", "app")):
            return "backend"
        if "worker" in primary_hint:
            return "worker"
        return "service"

    def _describe_support_service(self, item: DockerContainerRecord) -> str:
        service_name = item.compose_service or item.name
        role_label = {
            "cache": "cache",
            "database": "database",
            "queue": "queue",
            "ai-runtime": "ai runtime",
        }.get(item.service_role, item.service_role)
        return f"{service_name} ({role_label})"

    def _resolve_state(self, status: str) -> str:
        normalized = status.lower()
        if normalized.startswith("up"):
            return "running"
        if normalized.startswith("restarting"):
            return "restarting"
        if normalized.startswith("exited") or normalized.startswith("dead"):
            return "exited"
        if normalized.startswith("paused"):
            return "paused"
        if normalized.startswith("created"):
            return "created"
        return "unknown"

    def _resolve_health_status(self, status: str) -> str | None:
        normalized = status.lower()
        if "(healthy)" in normalized:
            return "healthy"
        if "(unhealthy)" in normalized:
            return "unhealthy"
        if "(health: starting)" in normalized or "(starting)" in normalized:
            return "starting"
        return None

    def _container_sort_key(self, item: DockerContainerRecord) -> tuple[int, int, str]:
        state_weight = {
            "restarting": 0,
            "exited": 1,
            "paused": 2,
            "created": 3,
            "running": 4,
            "unknown": 5,
        }
        health_weight = 0 if item.health_status == "unhealthy" else 1
        return (health_weight, state_weight.get(item.state, 99), item.name)

    def _application_sort_key(self, item: DockerApplicationRecord) -> tuple[int, int, str]:
        status_weight = {
            "degraded": 0,
            "partial": 1,
            "running": 2,
        }
        return (status_weight.get(item.status, 99), -item.total_containers, item.application_name)

    def _optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None