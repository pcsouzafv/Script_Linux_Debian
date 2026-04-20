import hmac

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings


def require_api_access(
    authorization: str | None = Header(default=None),
    api_key: str | None = Header(default=None, alias="X-Helpdesk-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    expected_token = settings.api_access_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A autenticação das rotas internas da API não foi configurada.",
        )

    provided_token: str | None = None
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.strip().lower() == "bearer" and credentials.strip():
            provided_token = credentials.strip()

    if not provided_token and api_key and api_key.strip():
        provided_token = api_key.strip()

    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas para a API.",
            headers={"WWW-Authenticate": "Bearer"},
        )