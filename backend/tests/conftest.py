import pytest

from app.core.config import get_settings
from app.services.intake import clear_user_intake_sessions


@pytest.fixture(autouse=True)
def isolate_test_settings() -> None:
    settings = get_settings()
    original_values = {
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
        "whatsapp_delivery_provider": settings.whatsapp_delivery_provider,
        "whatsapp_access_token": settings.whatsapp_access_token,
        "whatsapp_phone_number_id": settings.whatsapp_phone_number_id,
        "whatsapp_public_number": settings.whatsapp_public_number,
        "whatsapp_app_secret": settings.whatsapp_app_secret,
        "evolution_base_url": settings.evolution_base_url,
        "evolution_api_key": settings.evolution_api_key,
        "evolution_instance_name": settings.evolution_instance_name,
        "evolution_webhook_secret": settings.evolution_webhook_secret,
        "llm_enabled": settings.llm_enabled,
        "llm_base_url": settings.llm_base_url,
        "llm_api_key": settings.llm_api_key,
        "llm_model": settings.llm_model,
    }

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
    settings.whatsapp_delivery_provider = "mock"
    settings.whatsapp_access_token = None
    settings.whatsapp_phone_number_id = None
    settings.whatsapp_public_number = None
    settings.whatsapp_app_secret = None
    settings.evolution_base_url = None
    settings.evolution_api_key = None
    settings.evolution_instance_name = None
    settings.evolution_webhook_secret = None
    settings.llm_enabled = False
    settings.llm_base_url = None
    settings.llm_api_key = None
    settings.llm_model = None
    clear_user_intake_sessions()

    yield

    clear_user_intake_sessions()
    for key, value in original_values.items():
        setattr(settings, key, value)
