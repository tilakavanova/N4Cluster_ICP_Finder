"""API key authentication dependency."""

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from src.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency that enforces API key authentication.

    If API_KEY is not configured (empty), authentication is disabled (dev mode).
    """
    if not settings.api_key:
        # No API key configured — allow all requests (local dev)
        return "dev-mode"

    if not api_key or api_key != settings.api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key
