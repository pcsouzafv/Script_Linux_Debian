import json
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.services.glpi import GLPIClient, GLPIUserRecord
from app.schemas.helpdesk import IdentityLookupResponse, RequesterIdentity, UserRole
from app.services.exceptions import AuthorizationError, IntegrationError, ResourceNotFoundError


@dataclass(slots=True)
class ResolvedIdentity:
    requester: RequesterIdentity
    source: str
    notes: list[str]


class IdentityService:
    def __init__(self, settings: Settings, glpi_client: GLPIClient) -> None:
        self.settings = settings
        self.glpi_client = glpi_client
        self.base_dir = Path(__file__).resolve().parents[2]

    async def resolve_requester(
        self,
        phone_number: str,
        fallback_name: str | None = None,
        fallback_role: UserRole = UserRole.USER,
    ) -> ResolvedIdentity:
        if self.settings.identity_provider == "glpi":
            return await self._resolve_requester_from_glpi(phone_number)

        return self._resolve_requester_from_mock(
            phone_number=phone_number,
            fallback_name=fallback_name,
            fallback_role=fallback_role,
        )

    async def get_registered_identity(self, phone_number: str) -> IdentityLookupResponse:
        if self.settings.identity_provider == "glpi":
            user = await self.glpi_client.find_user_by_phone(phone_number)
            return self._build_identity_lookup_from_glpi(phone_number, user)

        directory = self._load_directory()
        normalized_phone = self._normalize_phone(phone_number)
        entry = directory.get(normalized_phone)
        if not entry:
            raise ResourceNotFoundError(
                f"Nenhuma identidade registrada para o número {phone_number}."
            )

        return IdentityLookupResponse(
            phone_number=phone_number,
            external_id=str(entry.get("external_id") or normalized_phone),
            display_name=entry.get("display_name"),
            role=UserRole(entry.get("role", UserRole.USER.value)),
            team=entry.get("team"),
            glpi_user_id=self._parse_glpi_user_id(entry.get("glpi_user_id")),
            source="directory",
            notes=["Identidade consultada no diretório local."],
        )

    async def get_registered_identity_by_identifier(
        self,
        identifier: str,
    ) -> IdentityLookupResponse:
        if self.settings.identity_provider == "glpi":
            user = await self.glpi_client.find_user_by_identifier(identifier)
            return self._build_identity_lookup_from_glpi(
                phone_number=user.mobile or user.phone or user.phone2 or identifier,
                user=user,
            )

        users = self._load_users()
        normalized_identifier = self._normalize_phone(identifier)
        lowered_identifier = identifier.strip().lower()

        for entry in users:
            phone_number = str(entry.get("phone_number") or "")
            external_id = str(entry.get("external_id") or "")
            display_name = str(entry.get("display_name") or "")

            if normalized_identifier and self._normalize_phone(phone_number) == normalized_identifier:
                return self._build_identity_lookup(phone_number, entry)
            if external_id and external_id.lower() == lowered_identifier:
                return self._build_identity_lookup(phone_number, entry)
            if display_name and display_name.lower() == lowered_identifier:
                return self._build_identity_lookup(phone_number, entry)

        raise ResourceNotFoundError(
            f"Nenhuma identidade registrada para o identificador {identifier}."
        )

    async def get_requester_by_glpi_user_id(
        self,
        glpi_user_id: int,
    ) -> ResolvedIdentity:
        if glpi_user_id <= 0:
            raise ResourceNotFoundError(
                f"Nenhuma identidade registrada para o glpi_user_id {glpi_user_id}."
            )

        if self.settings.identity_provider == "glpi":
            user = await self.glpi_client.find_user_by_id(glpi_user_id)
            requester = RequesterIdentity(
                external_id=user.login or str(user.user_id),
                display_name=self._build_display_name(user),
                phone_number=user.mobile or user.phone or user.phone2,
                role=self._resolve_role_from_profiles(user.profile_names),
                team=user.group_names[0] if user.group_names else None,
                glpi_user_id=user.user_id,
            )
            return ResolvedIdentity(
                requester=requester,
                source="glpi",
                notes=["Identidade resolvida diretamente a partir do GLPI pelo glpi_user_id."],
            )

        users = self._load_users()
        for entry in users:
            if self._parse_glpi_user_id(entry.get("glpi_user_id")) != glpi_user_id:
                continue
            requester = RequesterIdentity(
                external_id=str(entry.get("external_id") or glpi_user_id),
                display_name=entry.get("display_name"),
                phone_number=entry.get("phone_number"),
                role=UserRole(entry.get("role", UserRole.USER.value)),
                team=entry.get("team"),
                glpi_user_id=glpi_user_id,
            )
            return ResolvedIdentity(
                requester=requester,
                source="directory",
                notes=["Identidade resolvida a partir do diretório local via glpi_user_id."],
            )

        raise ResourceNotFoundError(
            f"Nenhuma identidade registrada para o glpi_user_id {glpi_user_id}."
        )

    def _resolve_requester_from_mock(
        self,
        phone_number: str,
        fallback_name: str | None = None,
        fallback_role: UserRole = UserRole.USER,
    ) -> ResolvedIdentity:
        directory = self._load_directory()
        normalized_phone = self._normalize_phone(phone_number)
        entry = directory.get(normalized_phone)

        if entry:
            requester = RequesterIdentity(
                external_id=str(entry.get("external_id") or normalized_phone),
                display_name=entry.get("display_name") or fallback_name,
                phone_number=phone_number,
                role=UserRole(entry.get("role", UserRole.USER.value)),
                team=entry.get("team"),
                glpi_user_id=self._parse_glpi_user_id(entry.get("glpi_user_id")),
            )
            notes = ["Identidade resolvida a partir do diretório local de usuários."]
            if requester.glpi_user_id:
                notes.append(
                    f"Solicitante mapeado ao usuário GLPI {requester.glpi_user_id}."
                )
            return ResolvedIdentity(
                requester=requester,
                source="directory",
                notes=notes,
            )

        requester = RequesterIdentity(
            external_id=normalized_phone or phone_number,
            display_name=fallback_name,
            phone_number=phone_number,
            role=fallback_role,
        )
        return ResolvedIdentity(
            requester=requester,
            source="fallback",
            notes=["Número não encontrado no diretório local; aplicado fallback do payload."],
        )

    async def _resolve_requester_from_glpi(self, phone_number: str) -> ResolvedIdentity:
        try:
            identity = await self.get_registered_identity(phone_number)
        except ResourceNotFoundError as exc:
            raise AuthorizationError(
                f"O número {phone_number} não está autorizado para abrir chamado porque não foi encontrado no GLPI."
            ) from exc

        requester = RequesterIdentity(
            external_id=identity.external_id,
            display_name=identity.display_name,
            phone_number=phone_number,
            role=identity.role,
            team=identity.team,
            glpi_user_id=identity.glpi_user_id,
        )
        return ResolvedIdentity(
            requester=requester,
            source="glpi",
            notes=identity.notes,
        )

    def _load_directory(self) -> dict[str, dict]:
        mapped_directory: dict[str, dict] = {}
        for item in self._load_users():
            phone_number = str(item.get("phone_number") or "")
            normalized_phone = self._normalize_phone(phone_number)
            if not normalized_phone:
                continue
            mapped_directory[normalized_phone] = item

        return mapped_directory

    def _load_users(self) -> list[dict]:
        path = self._resolve_store_path()
        if not path.exists():
            return []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise IntegrationError(
                f"Arquivo de identidades inválido em {path}: {exc}"
            ) from exc
        except OSError as exc:
            raise IntegrationError(
                f"Falha ao ler o diretório de identidades em {path}: {exc}"
            ) from exc

        users = data.get("users")
        if users is None:
            return []
        if not isinstance(users, list):
            raise IntegrationError(
                f"O arquivo de identidades em {path} deve conter uma lista em 'users'."
            )

        filtered_users: list[dict] = []
        for item in users:
            if not isinstance(item, dict):
                continue
            if item.get("active", True) is False:
                continue
            filtered_users.append(item)
        return filtered_users

    def _resolve_store_path(self) -> Path:
        configured_path = Path(self.settings.identity_store_path)
        if configured_path.is_absolute():
            return configured_path
        return self.base_dir / configured_path

    def _normalize_phone(self, phone_number: str) -> str:
        return "".join(character for character in str(phone_number) if character.isdigit())

    def _parse_glpi_user_id(self, value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            parsed_value = int(value)
        except (TypeError, ValueError):
            return None
        return parsed_value if parsed_value > 0 else None

    def _build_identity_lookup(
        self,
        phone_number: str,
        entry: dict,
    ) -> IdentityLookupResponse:
        return IdentityLookupResponse(
            phone_number=phone_number,
            external_id=str(entry.get("external_id") or self._normalize_phone(phone_number)),
            display_name=entry.get("display_name"),
            role=UserRole(entry.get("role", UserRole.USER.value)),
            team=entry.get("team"),
            glpi_user_id=self._parse_glpi_user_id(entry.get("glpi_user_id")),
            source="directory",
            notes=["Identidade consultada no diretório local."],
        )

    def _build_identity_lookup_from_glpi(
        self,
        phone_number: str,
        user: GLPIUserRecord,
    ) -> IdentityLookupResponse:
        role = self._resolve_role_from_profiles(user.profile_names)
        display_name = self._build_display_name(user)
        notes = [
            "Identidade resolvida diretamente a partir do GLPI.",
            f"Perfis GLPI detectados: {', '.join(user.profile_names) or 'nenhum'}.",
        ]
        if user.group_names:
            notes.append(f"Grupos GLPI detectados: {', '.join(user.group_names)}.")

        return IdentityLookupResponse(
            phone_number=phone_number,
            external_id=user.login or str(user.user_id),
            display_name=display_name,
            role=role,
            team=user.group_names[0] if user.group_names else None,
            glpi_user_id=user.user_id,
            source="glpi",
            notes=notes,
        )

    def _build_display_name(self, user: GLPIUserRecord) -> str | None:
        parts = [part for part in (user.firstname, user.realname) if part]
        if parts:
            return " ".join(parts)
        return user.login or None

    def _resolve_role_from_profiles(self, profile_names: list[str]) -> UserRole:
        normalized_profiles = {profile.strip().lower() for profile in profile_names if profile.strip()}
        if self._matches_any_profile(normalized_profiles, self.settings.identity_glpi_admin_profiles):
            return UserRole.ADMIN
        if self._matches_any_profile(normalized_profiles, self.settings.identity_glpi_supervisor_profiles):
            return UserRole.SUPERVISOR
        if self._matches_any_profile(normalized_profiles, self.settings.identity_glpi_technician_profiles):
            return UserRole.TECHNICIAN
        if self._matches_any_profile(normalized_profiles, self.settings.identity_glpi_user_profiles):
            return UserRole.USER
        return UserRole.USER

    def _matches_any_profile(
        self,
        normalized_profiles: set[str],
        configured_profiles: list[str],
    ) -> bool:
        normalized_configured = {
            profile.strip().lower() for profile in configured_profiles if profile.strip()
        }
        if not normalized_configured:
            return False
        return not normalized_profiles.isdisjoint(normalized_configured)