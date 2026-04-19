import asyncio

import pytest

from app.core.config import Settings
from app.services.exceptions import AuthorizationError, ResourceNotFoundError
from app.services.glpi import GLPIUserRecord
from app.services.identity import IdentityService


class FakeGLPIClient:
    configured = True

    def __init__(self) -> None:
        self.user_by_phone = GLPIUserRecord(
            user_id=42,
            login="maria.santos",
            firstname="Maria",
            realname="Santos",
            mobile="+5521997775269",
            profile_names=["Self-Service"],
            group_names=["financeiro"],
        )

    async def find_user_by_phone(self, phone_number: str) -> GLPIUserRecord:
        if phone_number in {
            self.user_by_phone.mobile,
            self.user_by_phone.phone,
            self.user_by_phone.phone2,
        }:
            return self.user_by_phone
        raise ResourceNotFoundError(f"Nenhum usuário GLPI ativo encontrado para número {phone_number}.")

    async def find_user_by_identifier(self, identifier: str) -> GLPIUserRecord:
        if identifier in {"maria.santos", "Maria Santos", "+5521997775269"}:
            return self.user_by_phone
        raise ResourceNotFoundError(f"Nenhum usuário GLPI ativo encontrado para identificador {identifier}.")

    async def find_user_by_id(self, user_id: int) -> GLPIUserRecord:
        if user_id == self.user_by_phone.user_id:
            return self.user_by_phone
        raise ResourceNotFoundError(f"Usuário GLPI {user_id} não encontrado.")


def test_identity_service_resolves_requester_from_glpi_phone() -> None:
    settings = Settings(identity_provider="glpi")
    service = IdentityService(settings, FakeGLPIClient())

    resolved = asyncio.run(
        service.resolve_requester(phone_number="+5521997775269")
    )

    assert resolved.source == "glpi"
    assert resolved.requester.external_id == "maria.santos"
    assert resolved.requester.display_name == "Maria Santos"
    assert resolved.requester.role.value == "user"
    assert resolved.requester.glpi_user_id == 42


def test_identity_service_rejects_unknown_phone_when_using_glpi() -> None:
    settings = Settings(identity_provider="glpi")
    service = IdentityService(settings, FakeGLPIClient())

    with pytest.raises(AuthorizationError):
        asyncio.run(service.resolve_requester(phone_number="+5511000000000"))


def test_identity_service_maps_technician_profile_from_glpi() -> None:
    settings = Settings(identity_provider="glpi")
    client = FakeGLPIClient()
    client.user_by_phone = GLPIUserRecord(
        user_id=77,
        login="ana.souza",
        firstname="Ana",
        realname="Souza",
        mobile="+5511912345678",
        profile_names=["Technician"],
        group_names=["infraestrutura"],
    )
    service = IdentityService(settings, client)

    identity = asyncio.run(service.get_registered_identity("+5511912345678"))

    assert identity.role.value == "technician"
    assert identity.team == "infraestrutura"


def test_identity_service_finds_user_by_identifier_in_glpi() -> None:
    settings = Settings(identity_provider="glpi")
    service = IdentityService(settings, FakeGLPIClient())

    identity = asyncio.run(service.get_registered_identity_by_identifier("Maria Santos"))

    assert identity.external_id == "maria.santos"
    assert identity.glpi_user_id == 42


def test_identity_service_finds_requester_by_glpi_user_id() -> None:
    settings = Settings(identity_provider="glpi")
    service = IdentityService(settings, FakeGLPIClient())

    resolved = asyncio.run(service.get_requester_by_glpi_user_id(42))

    assert resolved.source == "glpi"
    assert resolved.requester.external_id == "maria.santos"
    assert resolved.requester.phone_number == "+5521997775269"
    assert resolved.requester.glpi_user_id == 42


def test_identity_service_maps_supervisor_from_configured_glpi_profile() -> None:
    settings = Settings(
        identity_provider="glpi",
        identity_glpi_supervisor_profiles=["Support Supervisor"],
        identity_glpi_admin_profiles=["Platform Admin"],
    )
    client = FakeGLPIClient()
    client.user_by_phone = GLPIUserRecord(
        user_id=88,
        login="paula.almeida",
        firstname="Paula",
        realname="Almeida",
        mobile="+5521972008679",
        profile_names=["Support Supervisor"],
        group_names=["service-desk"],
    )
    service = IdentityService(settings, client)

    identity = asyncio.run(service.get_registered_identity("+5521972008679"))

    assert identity.role.value == "supervisor"


def test_identity_service_can_remap_super_admin_to_supervisor() -> None:
    settings = Settings(
        identity_provider="glpi",
        identity_glpi_supervisor_profiles=["Super-Admin"],
        identity_glpi_admin_profiles=[],
    )
    client = FakeGLPIClient()
    client.user_by_phone = GLPIUserRecord(
        user_id=99,
        login="paula.almeida",
        firstname="Paula",
        realname="Almeida",
        mobile="+5521972008679",
        profile_names=["Super-Admin"],
        group_names=["service-desk"],
    )
    service = IdentityService(settings, client)

    identity = asyncio.run(service.get_registered_identity("+5521972008679"))

    assert identity.role.value == "supervisor"


def test_settings_parse_csv_identity_profiles_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HELPDESK_IDENTITY_GLPI_SUPERVISOR_PROFILES",
        "Super-Admin,Support Supervisor",
    )
    monkeypatch.setenv("HELPDESK_IDENTITY_GLPI_ADMIN_PROFILES", "")

    settings = Settings(_env_file=None)

    assert settings.identity_glpi_supervisor_profiles == [
        "Super-Admin",
        "Support Supervisor",
    ]
    assert settings.identity_glpi_admin_profiles == []