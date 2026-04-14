"""OAuth2/JWT authentication endpoints (NIF-254).

Endpoints:
  POST /auth/token               — issue access token (OAuth2 client_credentials)
  POST /auth/token/refresh       — re-issue a token from a valid one
  POST /auth/clients             — admin: create API client
  DELETE /auth/clients/{id}      — admin: deactivate client
  POST /auth/clients/{id}/rotate — rotate client secret
"""

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth, require_scope
from src.db.session import get_session
from src.services import auth_service
from src.utils.logging import get_logger

logger = get_logger("auth_router")

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Token endpoints
# ---------------------------------------------------------------------------

@router.post("/token")
async def issue_token(
    client_id: str = Form(...),
    client_secret: str = Form(...),
    scope: str = Form(default=""),
    db: AsyncSession = Depends(get_session),
):
    """Issue a Bearer token using client credentials (OAuth2 client_credentials flow)."""
    client = await auth_service.authenticate_client(client_id, client_secret, db)
    if client is None:
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    # Requested scopes — intersect with what the client is allowed
    requested = [s.strip() for s in scope.split() if s.strip()]
    effective_scopes = (
        [s for s in requested if s in (client.scopes or [])]
        if requested
        else (client.scopes or [])
    )

    expires_in = 60 * 60  # 1 hour
    token = auth_service.create_token(client, scopes=effective_scopes, expires_in=expires_in)

    await auth_service.persist_token(token, client, effective_scopes, expires_in, db)

    logger.info("token_issued", client_id=client_id, scopes=effective_scopes)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "scope": " ".join(effective_scopes),
    }


@router.post("/token/refresh")
async def refresh_token(
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_session),
):
    """Re-issue a new token from a currently valid Bearer token."""
    if auth.get("mode") != "jwt":
        raise HTTPException(
            status_code=400,
            detail="Token refresh requires a Bearer JWT token",
        )

    client_id: str = auth["sub"]
    scopes: list[str] = auth.get("scopes", [])

    from sqlalchemy import select
    from src.db.auth_models import APIClient

    result = await db.execute(
        select(APIClient).where(APIClient.client_id == client_id)
    )
    client = result.scalar_one_or_none()
    if client is None or not client.is_active:
        raise HTTPException(status_code=401, detail="Client no longer active")

    expires_in = 60 * 60
    new_token = auth_service.create_token(client, scopes=scopes, expires_in=expires_in)
    await auth_service.persist_token(new_token, client, scopes, expires_in, db)

    return {
        "access_token": new_token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "scope": " ".join(scopes),
    }


# ---------------------------------------------------------------------------
# Client management (admin-only)
# ---------------------------------------------------------------------------

@router.post("/clients", status_code=201)
async def create_client(
    name: str = Form(...),
    scopes: str = Form(default="leads:read"),
    rate_limit_per_minute: int = Form(default=60),
    _auth: dict = Depends(require_scope("admin:all")),
    db: AsyncSession = Depends(get_session),
):
    """Create a new API client. Requires admin:all scope."""
    scope_list = [s.strip() for s in scopes.split() if s.strip()]
    invalid = set(scope_list) - auth_service.VALID_SCOPES
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown scopes: {', '.join(sorted(invalid))}",
        )

    client_id, raw_secret = await auth_service.create_client(
        name=name,
        scopes=scope_list,
        db=db,
        rate_limit_per_minute=rate_limit_per_minute,
    )
    return {
        "client_id": client_id,
        "client_secret": raw_secret,
        "scopes": scope_list,
        "rate_limit_per_minute": rate_limit_per_minute,
        "message": "Store the client_secret — it will not be shown again.",
    }


@router.delete("/clients/{client_id}", status_code=200)
async def deactivate_client(
    client_id: str,
    _auth: dict = Depends(require_scope("admin:all")),
    db: AsyncSession = Depends(get_session),
):
    """Deactivate an API client. Requires admin:all scope."""
    found = await auth_service.deactivate_client(client_id, db)
    if not found:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"detail": "Client deactivated", "client_id": client_id}


@router.post("/clients/{client_id}/rotate", status_code=200)
async def rotate_secret(
    client_id: str,
    _auth: dict = Depends(require_scope("admin:all")),
    db: AsyncSession = Depends(get_session),
):
    """Rotate the client secret. Returns the new secret once. Requires admin:all scope."""
    new_secret = await auth_service.rotate_client_secret(client_id, db)
    if new_secret is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return {
        "client_id": client_id,
        "client_secret": new_secret,
        "message": "Store the new client_secret — it will not be shown again.",
    }
