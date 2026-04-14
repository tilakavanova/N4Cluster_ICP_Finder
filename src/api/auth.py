"""API authentication dependencies (NIF-171, NIF-254).

Supports two auth modes:
  - Legacy: X-API-Key header matching the API_KEY env var
  - JWT Bearer: Authorization: Bearer <jwt> with optional scope checking
"""

import jwt
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from src.config import settings
from src.services import auth_service

# ---------------------------------------------------------------------------
# Security scheme objects
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Legacy dependency (kept for backward compat, used internally)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Unified auth dependency
# ---------------------------------------------------------------------------

async def require_auth(
    api_key: str | None = Security(api_key_header),
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict:
    """Accept either legacy X-API-Key or a Bearer JWT token.

    Returns a context dict:
      - legacy mode:  {"mode": "legacy", "sub": "legacy"}
      - dev mode:     {"mode": "dev",    "sub": "dev-mode"}
      - JWT mode:     the decoded JWT payload dict + {"mode": "jwt"}
    """
    # --- dev mode (no key configured at all) ---
    if not settings.api_key and not settings.effective_jwt_secret:
        return {"mode": "dev", "sub": "dev-mode", "scopes": list(auth_service.VALID_SCOPES)}

    # --- legacy X-API-Key (only when API_KEY is configured) ---
    if settings.api_key and api_key and api_key == settings.api_key:
        return {"mode": "legacy", "sub": "legacy", "scopes": list(auth_service.VALID_SCOPES)}

    # --- Bearer JWT ---
    if credentials and credentials.scheme.lower() == "bearer":
        try:
            payload = auth_service.verify_token(credentials.credentials)
            payload["mode"] = "jwt"
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=401,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=401,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # --- no valid credential found ---
    # Dev mode: if api_key env var is empty, allow all (backwards compat)
    if not settings.api_key:
        return {"mode": "dev", "sub": "dev-mode", "scopes": list(auth_service.VALID_SCOPES)}

    raise HTTPException(
        status_code=401,
        detail="Authentication required",
        headers={"WWW-Authenticate": 'Bearer, ApiKey realm="X-API-Key"'},
    )


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------

def require_scope(scope: str):
    """Return a FastAPI dependency that checks the JWT payload has *scope*.

    Legacy and dev-mode contexts have all scopes granted.
    """
    async def _check(auth: dict = Depends(require_auth)) -> dict:
        mode = auth.get("mode", "")
        if mode in ("legacy", "dev"):
            return auth  # Full access

        token_scopes: list[str] = auth.get("scopes", [])
        if scope not in token_scopes and "admin:all" not in token_scopes:
            raise HTTPException(
                status_code=403,
                detail=f"Scope '{scope}' required",
            )
        return auth

    return _check
