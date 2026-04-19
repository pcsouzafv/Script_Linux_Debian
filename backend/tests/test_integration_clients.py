from app.core.config import Settings
from app.services.glpi import GLPIClient
from app.services.zabbix import ZabbixClient


def test_glpi_client_is_configured_with_user_token() -> None:
    settings = Settings(
        glpi_base_url="http://127.0.0.1:8088/apirest.php",
        glpi_user_token="token-123",
    )

    assert GLPIClient(settings).configured is True


def test_glpi_client_is_configured_with_username_and_password() -> None:
    settings = Settings(
        glpi_base_url="http://127.0.0.1:8088/apirest.php",
        glpi_username="glpi",
        glpi_password="glpi",
    )

    assert GLPIClient(settings).configured is True


def test_zabbix_client_is_configured_with_api_token() -> None:
    settings = Settings(
        zabbix_base_url="http://127.0.0.1:8089/api_jsonrpc.php",
        zabbix_api_token="token-123",
    )

    assert ZabbixClient(settings).configured is True


def test_zabbix_client_is_configured_with_username_and_password() -> None:
    settings = Settings(
        zabbix_base_url="http://127.0.0.1:8089/api_jsonrpc.php",
        zabbix_username="Admin",
        zabbix_password="zabbix",
    )

    assert ZabbixClient(settings).configured is True
