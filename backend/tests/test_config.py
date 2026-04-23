import pytest

from app.core.config import Settings


def test_settings_rejects_duplicate_current_and_previous_api_tokens() -> None:
    with pytest.raises(ValueError, match="API_ACCESS_TOKEN_PREVIOUS"):
        Settings(
            _env_file=None,
            api_access_token="same-token",
            api_access_token_previous="same-token",
        )


def test_settings_rejects_reusing_api_token_for_audit_scope() -> None:
    with pytest.raises(ValueError, match="auditoria"):
        Settings(
            _env_file=None,
            api_access_token="shared-token",
            audit_access_token="shared-token",
        )


def test_settings_rejects_previous_audit_token_without_current_token() -> None:
    with pytest.raises(ValueError, match="AUDIT_ACCESS_TOKEN"):
        Settings(
            _env_file=None,
            audit_access_token_previous="legacy-audit-token",
        )


def test_settings_rejects_reusing_automation_token_for_api_scope() -> None:
    with pytest.raises(ValueError, match="automacao"):
        Settings(
            _env_file=None,
            api_access_token="shared-token",
            automation_access_token="shared-token",
        )


def test_settings_rejects_previous_automation_token_without_current_token() -> None:
    with pytest.raises(ValueError, match="AUTOMATION_ACCESS_TOKEN"):
        Settings(
            _env_file=None,
            automation_access_token_previous="legacy-automation-token",
        )


def test_settings_rejects_reusing_automation_read_token_for_write_scope() -> None:
    with pytest.raises(ValueError, match="leitura de automacao"):
        Settings(
            _env_file=None,
            automation_access_token="shared-token",
            automation_read_access_token="shared-token",
        )


def test_settings_rejects_previous_automation_read_token_without_current_token() -> None:
    with pytest.raises(ValueError, match="AUTOMATION_READ_ACCESS_TOKEN"):
        Settings(
            _env_file=None,
            automation_read_access_token_previous="legacy-automation-read-token",
        )


def test_settings_rejects_reusing_approval_token_for_automation_scope() -> None:
    with pytest.raises(ValueError, match="aprovacao"):
        Settings(
            _env_file=None,
            automation_access_token="shared-token",
            automation_approval_access_token="shared-token",
        )


def test_settings_rejects_previous_approval_token_without_current_token() -> None:
    with pytest.raises(ValueError, match="AUTOMATION_APPROVAL_ACCESS_TOKEN"):
        Settings(
            _env_file=None,
            automation_approval_access_token_previous="legacy-approval-token",
        )


def test_settings_rejects_invalid_automation_worker_max_attempts() -> None:
    with pytest.raises(ValueError, match="MAX_ATTEMPTS"):
        Settings(
            _env_file=None,
            automation_worker_max_attempts=0,
        )


def test_settings_rejects_invalid_operational_payload_max_depth() -> None:
    with pytest.raises(ValueError, match="PAYLOAD_MAX_DEPTH"):
        Settings(
            _env_file=None,
            operational_payload_max_depth=0,
        )


def test_settings_rejects_invalid_operational_job_retention_days() -> None:
    with pytest.raises(ValueError, match="JOB_RETENTION_DAYS"):
        Settings(
            _env_file=None,
            operational_job_retention_days=-1,
        )


def test_settings_rejects_invalid_automation_approval_timeout_minutes() -> None:
    with pytest.raises(ValueError, match="APPROVAL_TIMEOUT_MINUTES"):
        Settings(
            _env_file=None,
            automation_approval_timeout_minutes=-1,
        )


def test_settings_rejects_invalid_automation_retry_base_seconds() -> None:
    with pytest.raises(ValueError, match="RETRY_BASE_SECONDS"):
        Settings(
            _env_file=None,
            automation_retry_base_seconds=0,
        )


def test_settings_rejects_retry_max_smaller_than_retry_base() -> None:
    with pytest.raises(ValueError, match="RETRY_MAX_SECONDS"):
        Settings(
            _env_file=None,
            automation_retry_base_seconds=30,
            automation_retry_max_seconds=10,
        )


def test_settings_rejects_invalid_automation_runner_timeout() -> None:
    with pytest.raises(ValueError, match="TIMEOUT_SECONDS"):
        Settings(
            _env_file=None,
            automation_runner_timeout_seconds=0,
        )


def test_settings_parse_glpi_queue_group_map_from_json() -> None:
    settings = Settings(
        _env_file=None,
        glpi_queue_group_map='{"ServiceDesk-N1":"TI > Service Desk > N1","Infraestrutura-N1":"TI > Infraestrutura > N1"}',
    )

    assert settings.glpi_queue_group_map == {
        "ServiceDesk-N1": "TI > Service Desk > N1",
        "Infraestrutura-N1": "TI > Infraestrutura > N1",
    }


def test_settings_parse_glpi_queue_group_map_from_pairs() -> None:
    settings = Settings(
        _env_file=None,
        glpi_queue_group_map=(
            "ServiceDesk-Acessos=TI > Service Desk > Acessos;"
            "NOC-Critico=TI > NOC > Critico"
        ),
    )

    assert settings.glpi_queue_group_map == {
        "ServiceDesk-Acessos": "TI > Service Desk > Acessos",
        "NOC-Critico": "TI > NOC > Critico",
    }


def test_settings_parse_evolution_lid_phone_map_from_json() -> None:
    settings = Settings(
        _env_file=None,
        evolution_lid_phone_map='{"220095666237694@lid":"+5521972008679"}',
    )

    assert settings.evolution_lid_phone_map == {
        "220095666237694": "+5521972008679",
    }


def test_settings_parse_evolution_lid_phone_map_from_pairs() -> None:
    settings = Settings(
        _env_file=None,
        evolution_lid_phone_map=(
            "220095666237694@lid=+5521972008679;"
            "998877665544332=+5511912345678"
        ),
    )

    assert settings.evolution_lid_phone_map == {
        "220095666237694": "+5521972008679",
        "998877665544332": "+5511912345678",
    }


def test_settings_rejects_invalid_glpi_queue_group_map_pairs() -> None:
    with pytest.raises(ValueError, match="GLPI_QUEUE_GROUP_MAP"):
        Settings(
            _env_file=None,
            glpi_queue_group_map="ServiceDesk-N1",
        )


def test_settings_rejects_invalid_evolution_lid_phone_map_pairs() -> None:
    with pytest.raises(ValueError, match="EVOLUTION_LID_PHONE_MAP"):
        Settings(
            _env_file=None,
            evolution_lid_phone_map="220095666237694",
        )
