import json
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Helpdesk Orchestrator"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    api_host: str = "127.0.0.1"
    api_port: int = 18001
    api_port_max: int = 18010
    api_port_strict: bool = False
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

    zabbix_base_url: str | None = None
    zabbix_api_token: str | None = None
    zabbix_username: str | None = None
    zabbix_password: str | None = None

    whatsapp_verify_token: str = "local-verify-token"
    whatsapp_validate_signature: bool = False
    whatsapp_delivery_provider: str = "auto"
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_public_number: str | None = None
    whatsapp_app_secret: str | None = None

    evolution_base_url: str | None = None
    evolution_api_key: str | None = None
    evolution_instance_name: str | None = None
    evolution_webhook_secret: str | None = None

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
        "glpi_base_url",
        "glpi_app_token",
        "glpi_user_token",
        "glpi_username",
        "glpi_password",
        "zabbix_base_url",
        "zabbix_api_token",
        "zabbix_username",
        "zabbix_password",
        "whatsapp_access_token",
        "whatsapp_phone_number_id",
        "whatsapp_public_number",
        "whatsapp_app_secret",
        "evolution_base_url",
        "evolution_api_key",
        "evolution_instance_name",
        "evolution_webhook_secret",
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
