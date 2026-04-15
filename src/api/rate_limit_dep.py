"""FastAPI dependency for per-client rate limiting (NIF-255).

Reads rate_limit_per_minute from the authenticated APIClient (JWT mode)
or uses the global default (200/min) for legacy/dev-mode callers.

Usage:
    router = APIRouter(dependencies=[Depends(require_auth), Depends(rate_limit)])
    # — or per-endpoint —
    @router.get("/foo", dependencies=[Depends(rate_limit)])
    async def foo(): ...
"""

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.config import settings
from src.db.session import get_session
from src.utils.rate_limiter import check_rate_limit

# Default limit applied to legacy/dev callers (no per-client record)
_DEFAULT_LIMIT = 200


async def rate_limit(
    request: Request,
    response: Response,
    auth: dict = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Enforce per-client rate limit; attach X-RateLimit-* headers.

    Raises HTTP 429 with a Retry-After header when the limit is exceeded.
    """
    mode = auth.get("mode", "dev")
    client_id: str = auth.get("sub", "anonymous")

    # Determine limit from the APIClient record when using JWT auth
    limit = _DEFAULT_LIMIT
    if mode == "jwt":
        try:
            from src.db.auth_models import APIClient

            result = await session.execute(
                select(APIClient).where(APIClient.client_id == client_id)
            )
            api_client = result.scalar_one_or_none()
            if api_client:
                limit = api_client.rate_limit_per_minute
        except Exception:
            # If we can't look up the client, use the default
            pass

    allowed, remaining, reset_at = check_rate_limit(client_id, limit)

    # Always attach informational headers
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_at)

    if not allowed:
        retry_after = max(1, reset_at - int(__import__("time").time()))
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_at),
            },
        )
