import pytest

from app.core.config import get_settings
from app.services.glpi import MOCK_TICKET_STORE
from app.services.intake import clear_user_intake_sessions
from app.services.job_queue import clear_memory_job_queue
from app.services.operational_store import clear_memory_operational_state


@pytest.fixture(autouse=True)
def isolate_test_settings() -> None:
    settings = get_settings()
    original_values = {
        "api_access_token": settings.api_access_token,
        "api_access_token_previous": settings.api_access_token_previous,
        "audit_access_token": settings.audit_access_token,
        "audit_access_token_previous": settings.audit_access_token_previous,
        "automation_access_token": settings.automation_access_token,
        "automation_access_token_previous": settings.automation_access_token_previous,
        "automation_read_access_token": settings.automation_read_access_token,
        "automation_read_access_token_previous": settings.automation_read_access_token_previous,
        "automation_approval_access_token": settings.automation_approval_access_token,
        "automation_approval_access_token_previous": settings.automation_approval_access_token_previous,
        "identity_provider": settings.identity_provider,
        "identity_store_path": settings.identity_store_path,
        "glpi_base_url": settings.glpi_base_url,
        "glpi_app_token": settings.glpi_app_token,
        "glpi_user_token": settings.glpi_user_token,
        "glpi_username": settings.glpi_username,
        "glpi_password": settings.glpi_password,
        "zabbix_base_url": settings.zabbix_base_url,
        "zabbix_api_token": settings.zabbix_api_token,
        "zabbix_username": settings.zabbix_username,
        "zabbix_password": settings.zabbix_password,
        "whatsapp_verify_token": settings.whatsapp_verify_token,
        "whatsapp_validate_signature": settings.whatsapp_validate_signature,
        "whatsapp_delivery_provider": settings.whatsapp_delivery_provider,
        "whatsapp_access_token": settings.whatsapp_access_token,
        "whatsapp_phone_number_id": settings.whatsapp_phone_number_id,
        "whatsapp_public_number": settings.whatsapp_public_number,
        "whatsapp_app_secret": settings.whatsapp_app_secret,
        "evolution_base_url": settings.evolution_base_url,
        "evolution_api_key": settings.evolution_api_key,
        "evolution_instance_name": settings.evolution_instance_name,
        "evolution_webhook_secret": settings.evolution_webhook_secret,
        "operational_postgres_dsn": settings.operational_postgres_dsn,
        "operational_postgres_schema": settings.operational_postgres_schema,
        "operational_audit_retention_days": settings.operational_audit_retention_days,
        "operational_job_retention_days": settings.operational_job_retention_days,
        "automation_approval_timeout_minutes": settings.automation_approval_timeout_minutes,
        "operational_payload_max_depth": settings.operational_payload_max_depth,
        "operational_payload_max_list_items": settings.operational_payload_max_list_items,
        "operational_payload_max_object_keys": settings.operational_payload_max_object_keys,
        "operational_payload_max_string_length": settings.operational_payload_max_string_length,
        "redis_url": settings.redis_url,
        "automation_worker_max_attempts": settings.automation_worker_max_attempts,
        "automation_retry_base_seconds": settings.automation_retry_base_seconds,
        "automation_retry_max_seconds": settings.automation_retry_max_seconds,
        "automation_runner_base_dir": settings.automation_runner_base_dir,
        "automation_runner_timeout_seconds": settings.automation_runner_timeout_seconds,
        "llm_enabled": settings.llm_enabled,
        "llm_base_url": settings.llm_base_url,
        "llm_api_key": settings.llm_api_key,
        "llm_model": settings.llm_model,
    }

    settings.api_access_token = "test-api-token"
    settings.api_access_token_previous = None
    settings.audit_access_token = "test-audit-token"
    settings.audit_access_token_previous = None
    settings.automation_access_token = "test-automation-token"
    settings.automation_access_token_previous = None
    settings.automation_read_access_token = "test-automation-read-token"
    settings.automation_read_access_token_previous = None
    settings.automation_approval_access_token = "test-automation-approval-token"
    settings.automation_approval_access_token_previous = None
    settings.identity_provider = "mock-file"
    settings.identity_store_path = "data/identities.json"
    settings.glpi_base_url = None
    settings.glpi_app_token = None
    settings.glpi_user_token = None
    settings.glpi_username = None
    settings.glpi_password = None
    settings.zabbix_base_url = None
    settings.zabbix_api_token = None
    settings.zabbix_username = None
    settings.zabbix_password = None
    settings.whatsapp_verify_token = "test-whatsapp-verify-token"
    settings.whatsapp_validate_signature = False
    settings.whatsapp_delivery_provider = "mock"
    settings.whatsapp_access_token = None
    settings.whatsapp_phone_number_id = None
    settings.whatsapp_public_number = None
    settings.whatsapp_app_secret = None
    settings.evolution_base_url = None
    settings.evolution_api_key = None
    settings.evolution_instance_name = None
    settings.evolution_webhook_secret = None
    settings.operational_postgres_dsn = None
    settings.operational_postgres_schema = "helpdesk_platform"
    settings.operational_audit_retention_days = 30
    settings.operational_job_retention_days = 30
    settings.automation_approval_timeout_minutes = 1440
    settings.operational_payload_max_depth = 6
    settings.operational_payload_max_list_items = 20
    settings.operational_payload_max_object_keys = 50
    settings.operational_payload_max_string_length = 1024
    settings.redis_url = None
    settings.automation_worker_max_attempts = 3
    settings.automation_retry_base_seconds = 5
    settings.automation_retry_max_seconds = 300
    settings.automation_runner_base_dir = "../infra/automation-runner/projects"
    settings.automation_runner_timeout_seconds = 120
    settings.llm_enabled = False
    settings.llm_base_url = None
    settings.llm_api_key = None
    settings.llm_model = None
    clear_user_intake_sessions()
    clear_memory_operational_state()
    clear_memory_job_queue()
    MOCK_TICKET_STORE.clear()

    yield

    clear_user_intake_sessions()
    clear_memory_operational_state()
    clear_memory_job_queue()
    MOCK_TICKET_STORE.clear()
    for key, value in original_values.items():
        setattr(settings, key, value)
