import json
from functools import lru_cache
import re

from pydantic import model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Helpdesk Orchestrator"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    api_host: str = "127.0.0.1"
    api_port: int = 18001
    api_port_max: int = 18010
    api_port_strict: bool = False
    api_access_token: str | None = None
    api_access_token_previous: str | None = None
    audit_access_token: str | None = None
    audit_access_token_previous: str | None = None
    automation_access_token: str | None = None
    automation_access_token_previous: str | None = None
    automation_read_access_token: str | None = None
    automation_read_access_token_previous: str | None = None
    automation_approval_access_token: str | None = None
    automation_approval_access_token_previous: str | None = None
    identity_provider: str = "glpi"
    identity_store_path: str = "data/identities.json"
    identity_glpi_user_profiles: list[str] = ["Self-Service"]
    identity_glpi_technician_profiles: list[str] = ["Technician"]
    identity_glpi_supervisor_profiles: list[str] = ["Supervisor"]
    identity_glpi_admin_profiles: list[str] = ["Super-Admin", "Admin", "Administrator"]

    glpi_base_url: str | None = None
    glpi_app_token: str | None = None
    glpi_user_token: str | None = None
    glpi_username: str | None = None
    glpi_password: str | None = None
    glpi_queue_group_map: dict[str, str] = {}

    zabbix_base_url: str | None = None
    zabbix_api_token: str | None = None
    zabbix_username: str | None = None
    zabbix_password: str | None = None

    whatsapp_verify_token: str | None = None
    whatsapp_validate_signature: bool = True
    whatsapp_delivery_provider: str = "auto"
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_public_number: str | None = None
    whatsapp_app_secret: str | None = None

    evolution_base_url: str | None = None
    evolution_api_key: str | None = None
    evolution_instance_name: str | None = None
    evolution_webhook_secret: str | None = None
    evolution_lid_phone_map: dict[str, str] = {}

    operational_postgres_dsn: str | None = None
    operational_postgres_schema: str = "helpdesk_platform"
    operational_audit_retention_days: int | None = 30
    operational_job_retention_days: int | None = 30
    automation_approval_timeout_minutes: int | None = 1440
    operational_payload_max_depth: int = 6
    operational_payload_max_list_items: int = 20
    operational_payload_max_object_keys: int = 50
    operational_payload_max_string_length: int = 1024
    redis_url: str | None = None
    automation_worker_max_attempts: int = 3
    automation_retry_base_seconds: int = 5
    automation_retry_max_seconds: int = 300
    automation_runner_base_dir: str = "../infra/automation-runner/projects"
    automation_runner_timeout_seconds: int = 120

    llm_enabled: bool = False
    llm_provider: str = "ollama"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_request_timeout: float = 45.0
    llm_temperature: float = 0.2
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HELPDESK_",
        case_sensitive=False,
        enable_decoding=False,
    )

    @field_validator(
        "api_access_token",
        "api_access_token_previous",
        "audit_access_token",
        "audit_access_token_previous",
        "automation_access_token",
        "automation_access_token_previous",
        "automation_read_access_token",
        "automation_read_access_token_previous",
        "automation_approval_access_token",
        "automation_approval_access_token_previous",
        "glpi_base_url",
        "glpi_app_token",
        "glpi_user_token",
        "glpi_username",
        "glpi_password",
        "zabbix_base_url",
        "zabbix_api_token",
        "zabbix_username",
        "zabbix_password",
        "whatsapp_verify_token",
        "whatsapp_access_token",
        "whatsapp_phone_number_id",
        "whatsapp_public_number",
        "whatsapp_app_secret",
        "evolution_base_url",
        "evolution_api_key",
        "evolution_instance_name",
        "evolution_webhook_secret",
        "operational_postgres_dsn",
        "redis_url",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "openai_api_key",
        "groq_api_key",
        "gemini_api_key",
        "anthropic_api_key",
        mode="before",
    )
    @classmethod
    def normalize_optional_integration_values(cls, value: object) -> str | None:
        if value is None:
            return None

        normalized = str(value).strip()
        if not normalized:
            return None

        lowered = normalized.lower()
        placeholder_exact = {
            "placeholder",
            "preencher",
            "changeme",
            "change-me",
            "replace-me",
            "dummy",
            "todo",
            "tbd",
            "none",
            "null",
            "undefined",
        }
        placeholder_prefixes = (
            "preencher_",
            "placeholder_",
            "changeme_",
            "change-me_",
            "replace_",
            "replace-",
            "dummy_",
            "dummy-",
            "<",
        )

        if lowered in placeholder_exact or lowered.startswith(placeholder_prefixes):
            return None

        return normalized

    @field_validator("operational_postgres_schema", mode="before")
    @classmethod
    def normalize_operational_postgres_schema(cls, value: object) -> str:
        if value is None:
            return "helpdesk_platform"

        normalized = str(value).strip()
        if not normalized:
            return "helpdesk_platform"

        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized) is None:
            raise ValueError(
                "HELPDESK_OPERATIONAL_POSTGRES_SCHEMA deve usar apenas letras, numeros e underscore."
            )
        return normalized

    @field_validator("operational_audit_retention_days", mode="before")
    @classmethod
    def normalize_operational_audit_retention_days(cls, value: object) -> int | None:
        if value is None:
            return 30

        normalized = str(value).strip()
        if not normalized:
            return 30

        retention_days = int(normalized)
        if retention_days < 0:
            raise ValueError(
                "HELPDESK_OPERATIONAL_AUDIT_RETENTION_DAYS deve ser zero ou positivo."
            )
        if retention_days == 0:
            return None
        return retention_days

    @field_validator("operational_job_retention_days", mode="before")
    @classmethod
    def normalize_operational_job_retention_days(cls, value: object) -> int | None:
        if value is None:
            return 30

        normalized = str(value).strip()
        if not normalized:
            return 30

        retention_days = int(normalized)
        if retention_days < 0:
            raise ValueError(
                "HELPDESK_OPERATIONAL_JOB_RETENTION_DAYS deve ser zero ou positivo."
            )
        if retention_days == 0:
            return None
        return retention_days

    @field_validator("automation_approval_timeout_minutes", mode="before")
    @classmethod
    def normalize_automation_approval_timeout_minutes(cls, value: object) -> int | None:
        if value is None:
            return 1440

        normalized = str(value).strip()
        if not normalized:
            return 1440

        timeout_minutes = int(normalized)
        if timeout_minutes < 0:
            raise ValueError(
                "HELPDESK_AUTOMATION_APPROVAL_TIMEOUT_MINUTES deve ser zero ou positivo."
            )
        if timeout_minutes == 0:
            return None
        if timeout_minutes > 10080:
            raise ValueError(
                "HELPDESK_AUTOMATION_APPROVAL_TIMEOUT_MINUTES deve ficar abaixo de 10080 minutos."
            )
        return timeout_minutes

    @field_validator("operational_payload_max_depth", mode="before")
    @classmethod
    def normalize_operational_payload_max_depth(cls, value: object) -> int:
        if value is None:
            return 6

        normalized = str(value).strip()
        if not normalized:
            return 6

        max_depth = int(normalized)
        if max_depth <= 0:
            raise ValueError(
                "HELPDESK_OPERATIONAL_PAYLOAD_MAX_DEPTH deve ser maior que zero."
            )
        if max_depth > 12:
            raise ValueError(
                "HELPDESK_OPERATIONAL_PAYLOAD_MAX_DEPTH deve ficar abaixo de 12 niveis."
            )
        return max_depth

    @field_validator("operational_payload_max_list_items", mode="before")
    @classmethod
    def normalize_operational_payload_max_list_items(cls, value: object) -> int:
        if value is None:
            return 20

        normalized = str(value).strip()
        if not normalized:
            return 20

        max_items = int(normalized)
        if max_items <= 0:
            raise ValueError(
                "HELPDESK_OPERATIONAL_PAYLOAD_MAX_LIST_ITEMS deve ser maior que zero."
            )
        if max_items > 200:
            raise ValueError(
                "HELPDESK_OPERATIONAL_PAYLOAD_MAX_LIST_ITEMS deve ficar abaixo de 200 itens."
            )
        return max_items

    @field_validator("operational_payload_max_object_keys", mode="before")
    @classmethod
    def normalize_operational_payload_max_object_keys(cls, value: object) -> int:
        if value is None:
            return 50

        normalized = str(value).strip()
        if not normalized:
            return 50

        max_keys = int(normalized)
        if max_keys <= 0:
            raise ValueError(
                "HELPDESK_OPERATIONAL_PAYLOAD_MAX_OBJECT_KEYS deve ser maior que zero."
            )
        if max_keys > 200:
            raise ValueError(
                "HELPDESK_OPERATIONAL_PAYLOAD_MAX_OBJECT_KEYS deve ficar abaixo de 200 chaves."
            )
        return max_keys

    @field_validator("operational_payload_max_string_length", mode="before")
    @classmethod
    def normalize_operational_payload_max_string_length(cls, value: object) -> int:
        if value is None:
            return 1024

        normalized = str(value).strip()
        if not normalized:
            return 1024

        max_string_length = int(normalized)
        if max_string_length < 64:
            raise ValueError(
                "HELPDESK_OPERATIONAL_PAYLOAD_MAX_STRING_LENGTH deve ser de pelo menos 64 caracteres."
            )
        if max_string_length > 16384:
            raise ValueError(
                "HELPDESK_OPERATIONAL_PAYLOAD_MAX_STRING_LENGTH deve ficar abaixo de 16384 caracteres."
            )
        return max_string_length

    @field_validator("automation_worker_max_attempts", mode="before")
    @classmethod
    def normalize_automation_worker_max_attempts(cls, value: object) -> int:
        if value is None:
            return 3

        normalized = str(value).strip()
        if not normalized:
            return 3

        max_attempts = int(normalized)
        if max_attempts <= 0:
            raise ValueError("HELPDESK_AUTOMATION_WORKER_MAX_ATTEMPTS deve ser maior que zero.")
        if max_attempts > 10:
            raise ValueError(
                "HELPDESK_AUTOMATION_WORKER_MAX_ATTEMPTS deve ficar entre 1 e 10 para evitar retentativas excessivas."
            )
        return max_attempts

    @field_validator("automation_runner_base_dir", mode="before")
    @classmethod
    def normalize_automation_runner_base_dir(cls, value: object) -> str:
        if value is None:
            return "../infra/automation-runner/projects"

        normalized = str(value).strip()
        if not normalized:
            return "../infra/automation-runner/projects"
        return normalized

    @field_validator("automation_runner_timeout_seconds", mode="before")
    @classmethod
    def normalize_automation_runner_timeout_seconds(cls, value: object) -> int:
        if value is None:
            return 120

        normalized = str(value).strip()
        if not normalized:
            return 120

        timeout_seconds = int(normalized)
        if timeout_seconds <= 0:
            raise ValueError("HELPDESK_AUTOMATION_RUNNER_TIMEOUT_SECONDS deve ser maior que zero.")
        if timeout_seconds > 3600:
            raise ValueError(
                "HELPDESK_AUTOMATION_RUNNER_TIMEOUT_SECONDS deve ficar abaixo de 3600 segundos."
            )
        return timeout_seconds

    @field_validator("automation_retry_base_seconds", mode="before")
    @classmethod
    def normalize_automation_retry_base_seconds(cls, value: object) -> int:
        if value is None:
            return 5

        normalized = str(value).strip()
        if not normalized:
            return 5

        retry_base_seconds = int(normalized)
        if retry_base_seconds <= 0:
            raise ValueError(
                "HELPDESK_AUTOMATION_RETRY_BASE_SECONDS deve ser maior que zero."
            )
        if retry_base_seconds > 3600:
            raise ValueError(
                "HELPDESK_AUTOMATION_RETRY_BASE_SECONDS deve ficar abaixo de 3600 segundos."
            )
        return retry_base_seconds

    @field_validator("automation_retry_max_seconds", mode="before")
    @classmethod
    def normalize_automation_retry_max_seconds(cls, value: object) -> int:
        if value is None:
            return 300

        normalized = str(value).strip()
        if not normalized:
            return 300

        retry_max_seconds = int(normalized)
        if retry_max_seconds <= 0:
            raise ValueError(
                "HELPDESK_AUTOMATION_RETRY_MAX_SECONDS deve ser maior que zero."
            )
        if retry_max_seconds > 86400:
            raise ValueError(
                "HELPDESK_AUTOMATION_RETRY_MAX_SECONDS deve ficar abaixo de 86400 segundos."
            )
        return retry_max_seconds

    @model_validator(mode="after")
    def validate_retry_backoff_configuration(self) -> "Settings":
        if self.automation_retry_max_seconds < self.automation_retry_base_seconds:
            raise ValueError(
                "HELPDESK_AUTOMATION_RETRY_MAX_SECONDS deve ser maior ou igual a HELPDESK_AUTOMATION_RETRY_BASE_SECONDS."
            )
        return self

    @model_validator(mode="after")
    def validate_token_configuration(self) -> "Settings":
        scope_pairs = {
            "API_ACCESS_TOKEN": (
                self.api_access_token,
                self.api_access_token_previous,
            ),
            "AUDIT_ACCESS_TOKEN": (
                self.audit_access_token,
                self.audit_access_token_previous,
            ),
            "AUTOMATION_ACCESS_TOKEN": (
                self.automation_access_token,
                self.automation_access_token_previous,
            ),
            "AUTOMATION_READ_ACCESS_TOKEN": (
                self.automation_read_access_token,
                self.automation_read_access_token_previous,
            ),
            "AUTOMATION_APPROVAL_ACCESS_TOKEN": (
                self.automation_approval_access_token,
                self.automation_approval_access_token_previous,
            ),
        }
        normalized_tokens: dict[str, set[str]] = {}

        for scope_name, (current_token, previous_token) in scope_pairs.items():
            if previous_token and not current_token:
                raise ValueError(
                    f"HELPDESK_{scope_name} deve estar definido antes de HELPDESK_{scope_name}_PREVIOUS."
                )

            scope_tokens = {token for token in {current_token, previous_token} if token}
            if len(scope_tokens) == 1 and current_token and previous_token:
                raise ValueError(
                    f"HELPDESK_{scope_name}_PREVIOUS deve ser diferente do token atual."
                )

            normalized_tokens[scope_name] = scope_tokens

        api_tokens = normalized_tokens["API_ACCESS_TOKEN"]
        audit_tokens = normalized_tokens["AUDIT_ACCESS_TOKEN"]
        automation_tokens = normalized_tokens["AUTOMATION_ACCESS_TOKEN"]
        automation_read_tokens = normalized_tokens["AUTOMATION_READ_ACCESS_TOKEN"]
        automation_approval_tokens = normalized_tokens["AUTOMATION_APPROVAL_ACCESS_TOKEN"]

        if api_tokens & audit_tokens:
            raise ValueError(
                "Os tokens administrativos de auditoria devem ser diferentes dos tokens internos gerais da API."
            )

        if api_tokens & automation_tokens:
            raise ValueError(
                "Os tokens de automacao devem ser diferentes dos tokens internos gerais da API."
            )

        if audit_tokens & automation_tokens:
            raise ValueError(
                "Os tokens de automacao devem ser diferentes dos tokens administrativos de auditoria."
            )

        if api_tokens & automation_read_tokens:
            raise ValueError(
                "Os tokens de leitura de automacao devem ser diferentes dos tokens internos gerais da API."
            )

        if audit_tokens & automation_read_tokens:
            raise ValueError(
                "Os tokens de leitura de automacao devem ser diferentes dos tokens administrativos de auditoria."
            )

        if automation_tokens & automation_read_tokens:
            raise ValueError(
                "Os tokens de leitura de automacao devem ser diferentes dos tokens administrativos de criacao de automacao."
            )

        if api_tokens & automation_approval_tokens:
            raise ValueError(
                "Os tokens de aprovacao de automacao devem ser diferentes dos tokens internos gerais da API."
            )

        if audit_tokens & automation_approval_tokens:
            raise ValueError(
                "Os tokens de aprovacao de automacao devem ser diferentes dos tokens administrativos de auditoria."
            )

        if automation_read_tokens & automation_approval_tokens:
            raise ValueError(
                "Os tokens de aprovacao de automacao devem ser diferentes dos tokens administrativos de leitura de automacao."
            )

        if automation_tokens & automation_approval_tokens:
            raise ValueError(
                "Os tokens de aprovacao de automacao devem ser diferentes dos tokens administrativos gerais de automacao."
            )

        return self

    @field_validator("whatsapp_delivery_provider", mode="before")
    @classmethod
    def normalize_whatsapp_delivery_provider(cls, value: object) -> str:
        if value is None:
            return "auto"

        normalized = str(value).strip().lower()
        if not normalized:
            return "auto"

        aliases = {
            "auto": "auto",
            "automatic": "auto",
            "meta": "meta",
            "whatsapp-meta": "meta",
            "evolution": "evolution",
            "evolution-api": "evolution",
            "mock": "mock",
            "disabled": "mock",
            "local": "mock",
        }
        if normalized not in aliases:
            raise ValueError(
                "HELPDESK_WHATSAPP_DELIVERY_PROVIDER deve ser um de: auto, meta, evolution ou mock."
            )
        return aliases[normalized]

    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_llm_provider(cls, value: object) -> str:
        if value is None:
            return "ollama"

        normalized = str(value).strip().lower()
        if not normalized:
            return "ollama"

        aliases = {
            "ollama": "ollama",
            "local": "ollama",
            "openai": "openai",
            "openia": "openai",
            "groq": "groq",
            "gemini": "gemini",
            "google": "gemini",
            "claude": "claude",
            "anthropic": "claude",
        }
        if normalized not in aliases:
            raise ValueError(
                "HELPDESK_LLM_PROVIDER deve ser um de: ollama, openai, groq, gemini ou claude."
            )
        return aliases[normalized]

    @field_validator("identity_provider", mode="before")
    @classmethod
    def normalize_identity_provider(cls, value: object) -> str:
        if value is None:
            return "glpi"

        normalized = str(value).strip().lower()
        if not normalized:
            return "glpi"

        aliases = {
            "glpi": "glpi",
            "mock": "mock-file",
            "mock-file": "mock-file",
            "file": "mock-file",
        }
        if normalized not in aliases:
            raise ValueError(
                "HELPDESK_IDENTITY_PROVIDER deve ser 'glpi' ou 'mock-file'."
            )
        return aliases[normalized]

    @field_validator(
        "identity_glpi_user_profiles",
        "identity_glpi_technician_profiles",
        "identity_glpi_supervisor_profiles",
        "identity_glpi_admin_profiles",
        mode="before",
    )
    @classmethod
    def normalize_identity_profile_lists(cls, value: object) -> list[str]:
        if value is None:
            return []

        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]

        normalized = str(value).strip()
        if not normalized:
            return []

        if normalized.startswith("["):
            try:
                parsed = json.loads(normalized)
            except json.JSONDecodeError as exc:
                raise ValueError("Lista de perfis GLPI inválida em JSON.") from exc
            if not isinstance(parsed, list):
                raise ValueError("A configuração de perfis GLPI deve ser uma lista JSON ou CSV.")
            return [str(item).strip() for item in parsed if str(item).strip()]

        return [item.strip() for item in normalized.split(",") if item.strip()]

    @field_validator("glpi_queue_group_map", mode="before")
    @classmethod
    def normalize_glpi_queue_group_map(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}

        raw_items: object
        if isinstance(value, dict):
            raw_items = value.items()
        else:
            normalized = str(value).strip()
            if not normalized:
                return {}

            if normalized.startswith("{"):
                try:
                    parsed = json.loads(normalized)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "HELPDESK_GLPI_QUEUE_GROUP_MAP deve ser um objeto JSON ou uma lista de pares fila=grupo."
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ValueError(
                        "HELPDESK_GLPI_QUEUE_GROUP_MAP deve ser um objeto JSON ou uma lista de pares fila=grupo."
                    )
                raw_items = parsed.items()
            else:
                separator = ";" if ";" in normalized else ","
                pairs: list[tuple[str, str]] = []
                for chunk in normalized.split(separator):
                    entry = chunk.strip()
                    if not entry:
                        continue
                    if "=" not in entry:
                        raise ValueError(
                            "HELPDESK_GLPI_QUEUE_GROUP_MAP deve usar o formato fila=grupo."
                        )
                    queue_name, group_name = entry.split("=", 1)
                    pairs.append((queue_name, group_name))
                raw_items = pairs

        normalized_mapping: dict[str, str] = {}
        for queue_name, group_name in raw_items:
            normalized_queue = str(queue_name).strip()
            normalized_group = str(group_name).strip()
            if not normalized_queue or not normalized_group:
                continue
            normalized_mapping[normalized_queue] = normalized_group

        return normalized_mapping

    @field_validator("evolution_lid_phone_map", mode="before")
    @classmethod
    def normalize_evolution_lid_phone_map(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}

        raw_items: object
        if isinstance(value, dict):
            raw_items = value.items()
        else:
            normalized = str(value).strip()
            if not normalized:
                return {}

            if normalized.startswith("{"):
                try:
                    parsed = json.loads(normalized)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "HELPDESK_EVOLUTION_LID_PHONE_MAP deve ser um objeto JSON ou uma lista de pares lid=telefone."
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ValueError(
                        "HELPDESK_EVOLUTION_LID_PHONE_MAP deve ser um objeto JSON ou uma lista de pares lid=telefone."
                    )
                raw_items = parsed.items()
            else:
                separator = ";" if ";" in normalized else ","
                pairs: list[tuple[str, str]] = []
                for chunk in normalized.split(separator):
                    entry = chunk.strip()
                    if not entry:
                        continue
                    if "=" not in entry:
                        raise ValueError(
                            "HELPDESK_EVOLUTION_LID_PHONE_MAP deve usar o formato lid=telefone."
                        )
                    lid, phone_number = entry.split("=", 1)
                    pairs.append((lid, phone_number))
                raw_items = pairs

        normalized_mapping: dict[str, str] = {}
        for lid, phone_number in raw_items:
            normalized_lid = cls._normalize_evolution_lid_key(lid)
            normalized_phone = str(phone_number).strip()
            if not normalized_lid or not normalized_phone:
                continue
            if re.sub(r"\D+", "", normalized_phone) == "":
                raise ValueError(
                    "HELPDESK_EVOLUTION_LID_PHONE_MAP deve mapear cada LID para um telefone."
                )
            normalized_mapping[normalized_lid] = normalized_phone

        return normalized_mapping

    @staticmethod
    def _normalize_evolution_lid_key(value: object) -> str | None:
        normalized = str(value).strip()
        if not normalized:
            return None
        candidate = normalized.split("@", maxsplit=1)[0].split(":", maxsplit=1)[0]
        digits = re.sub(r"\D+", "", candidate)
        return digits or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
