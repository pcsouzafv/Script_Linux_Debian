import hmac

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings


def _resolve_provided_token(
    authorization: str | None,
    explicit_token: str | None,
) -> str | None:
    provided_token: str | None = None
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.strip().lower() == "bearer" and credentials.strip():
            provided_token = credentials.strip()

    if not provided_token and explicit_token and explicit_token.strip():
        provided_token = explicit_token.strip()

    return provided_token


def _require_token(
    *,
    expected_token: str | None,
    previous_token: str | None,
    missing_configuration_detail: str,
    invalid_credentials_detail: str,
    authorization: str | None,
    explicit_token: str | None,
) -> None:
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=missing_configuration_detail,
        )

    provided_token = _resolve_provided_token(authorization, explicit_token)
    allowed_tokens = [expected_token]
    if previous_token:
        allowed_tokens.append(previous_token)

    if not provided_token or not any(
        hmac.compare_digest(provided_token, allowed_token) for allowed_token in allowed_tokens
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=invalid_credentials_detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_api_access(
    authorization: str | None = Header(default=None),
    api_key: str | None = Header(default=None, alias="X-Helpdesk-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    _require_token(
        expected_token=settings.api_access_token,
        previous_token=settings.api_access_token_previous,
        missing_configuration_detail="A autenticação das rotas internas da API não foi configurada.",
        invalid_credentials_detail="Credenciais inválidas para a API.",
        authorization=authorization,
        explicit_token=api_key,
    )


def require_audit_access(
    authorization: str | None = Header(default=None),
    audit_key: str | None = Header(default=None, alias="X-Helpdesk-Audit-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    _require_token(
        expected_token=settings.audit_access_token,
        previous_token=settings.audit_access_token_previous,
        missing_configuration_detail=(
            "A autenticação administrativa da auditoria não foi configurada."
        ),
        invalid_credentials_detail="Credenciais inválidas para a auditoria administrativa.",
        authorization=authorization,
        explicit_token=audit_key,
    )


def require_automation_access(
    authorization: str | None = Header(default=None),
    automation_key: str | None = Header(default=None, alias="X-Helpdesk-Automation-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    _require_token(
        expected_token=settings.automation_access_token,
        previous_token=settings.automation_access_token_previous,
        missing_configuration_detail=(
            "A autenticação administrativa de automação não foi configurada."
        ),
        invalid_credentials_detail="Credenciais inválidas para a automação administrativa.",
        authorization=authorization,
        explicit_token=automation_key,
    )


def require_automation_read_access(
    authorization: str | None = Header(default=None),
    automation_read_key: str | None = Header(
        default=None,
        alias="X-Helpdesk-Automation-Read-Key",
    ),
    settings: Settings = Depends(get_settings),
) -> None:
    expected_token = settings.automation_read_access_token or settings.automation_access_token
    previous_token = (
        settings.automation_read_access_token_previous
        if settings.automation_read_access_token
        else settings.automation_access_token_previous
    )
    _require_token(
        expected_token=expected_token,
        previous_token=previous_token,
        missing_configuration_detail=(
            "A autenticação administrativa de leitura de automação não foi configurada."
        ),
        invalid_credentials_detail=(
            "Credenciais inválidas para a leitura administrativa de automação."
        ),
        authorization=authorization,
        explicit_token=automation_read_key,
    )


def require_automation_approval_access(
    authorization: str | None = Header(default=None),
    approval_key: str | None = Header(
        default=None,
        alias="X-Helpdesk-Automation-Approval-Key",
    ),
    settings: Settings = Depends(get_settings),
) -> None:
    _require_token(
        expected_token=settings.automation_approval_access_token,
        previous_token=settings.automation_approval_access_token_previous,
        missing_configuration_detail=(
            "A autenticação administrativa de aprovação de automação não foi configurada."
        ),
        invalid_credentials_detail="Credenciais inválidas para a aprovação administrativa de automação.",
        authorization=authorization,
        explicit_token=approval_key,
    )