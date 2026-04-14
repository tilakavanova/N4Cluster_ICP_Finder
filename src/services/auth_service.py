"""JWT/OAuth2 authentication service (NIF-254).

Handles API client registration, secret hashing, JWT issuance,
verification, revocation, and key rotation.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.auth_models import APIClient, APIToken
from src.utils.logging import get_logger

logger = get_logger("auth_service")

# Supported scope names
VALID_SCOPES = {
    "leads:read",
    "leads:write",
    "scoring:read",
    "scoring:write",
    "crawl:execute",
    "outreach:execute",
    "admin:all",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hash_secret(secret: str) -> str:
    """Return a bcrypt hash of *secret*."""
    return bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode()


def _verify_secret(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def _hash_token(token: str) -> str:
    """Return a SHA-256 hex digest of *token* for storage/lookup."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------

async def create_client(
    name: str,
    scopes: list[str],
    db: AsyncSession,
    rate_limit_per_minute: int = 60,
) -> tuple[str, str]:
    """Create a new API client.

    Returns (client_id, raw_client_secret). The secret is only returned once.
    """
    client_id = f"cid_{secrets.token_urlsafe(24)}"
    raw_secret = secrets.token_urlsafe(40)
    secret_hash = _hash_secret(raw_secret)

    client = APIClient(
        name=name,
        client_id=client_id,
        client_secret_hash=secret_hash,
        scopes=scopes,
        is_active=True,
        rate_limit_per_minute=rate_limit_per_minute,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    logger.info("api_client_created", name=name, client_id=client_id)
    return client_id, raw_secret


async def authenticate_client(
    client_id: str,
    client_secret: str,
    db: AsyncSession,
) -> Optional[APIClient]:
    """Return the APIClient if credentials are valid and client is active."""
    result = await db.execute(
        select(APIClient).where(APIClient.client_id == client_id)
    )
    client = result.scalar_one_or_none()

    if client is None or not client.is_active:
        return None
    if not _verify_secret(client_secret, client.client_secret_hash):
        return None

    # Update last_used_at
    await db.execute(
        update(APIClient)
        .where(APIClient.id == client.id)
        .values(last_used_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return client


async def deactivate_client(client_id: str, db: AsyncSession) -> bool:
    """Deactivate an API client. Returns True if client was found."""
    result = await db.execute(
        select(APIClient).where(APIClient.client_id == client_id)
    )
    client = result.scalar_one_or_none()
    if client is None:
        return False

    await db.execute(
        update(APIClient)
        .where(APIClient.id == client.id)
        .values(is_active=False)
    )
    await db.commit()
    logger.info("api_client_deactivated", client_id=client_id)
    return True


async def rotate_client_secret(
    client_id: str,
    db: AsyncSession,
) -> Optional[str]:
    """Rotate the client secret. Returns the new raw secret, or None if not found."""
    result = await db.execute(
        select(APIClient).where(APIClient.client_id == client_id)
    )
    client = result.scalar_one_or_none()
    if client is None:
        return None

    new_secret = secrets.token_urlsafe(40)
    new_hash = _hash_secret(new_secret)

    await db.execute(
        update(APIClient)
        .where(APIClient.id == client.id)
        .values(
            client_secret_hash=new_hash,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    logger.info("api_client_secret_rotated", client_id=client_id)
    return new_secret


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------

def create_token(
    client: APIClient,
    scopes: Optional[list[str]] = None,
    expires_in: int = 3600,
) -> str:
    """Issue a signed JWT for *client*.

    *scopes* defaults to the client's registered scopes.
    Returns the raw JWT string.
    """
    effective_scopes = scopes if scopes is not None else (client.scopes or [])
    token_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=expires_in)

    payload = {
        "sub": client.client_id,
        "scopes": effective_scopes,
        "exp": exp,
        "iat": now,
        "jti": token_id,
    }

    token = jwt.encode(
        payload,
        settings.effective_jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token


async def persist_token(
    token: str,
    client: APIClient,
    scopes: list[str],
    expires_in: int,
    db: AsyncSession,
) -> APIToken:
    """Store a token record so it can be revoked later."""
    token_payload = verify_token(token)
    token_id = uuid.UUID(token_payload["jti"])
    exp = datetime.fromtimestamp(token_payload["exp"], tz=timezone.utc)

    record = APIToken(
        id=token_id,
        client_id=client.id,
        token_hash=_hash_token(token),
        scopes=scopes,
        expires_at=exp,
    )
    db.add(record)
    await db.commit()
    return record


def verify_token(token: str) -> dict:
    """Decode and validate a JWT. Raises jwt.InvalidTokenError on failure."""
    payload = jwt.decode(
        token,
        settings.effective_jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    return payload


async def is_token_revoked(token: str, db: AsyncSession) -> bool:
    """Return True if the token has been explicitly revoked."""
    token_hash = _hash_token(token)
    result = await db.execute(
        select(APIToken).where(
            APIToken.token_hash == token_hash,
            APIToken.revoked_at.isnot(None),
        )
    )
    return result.scalar_one_or_none() is not None


async def revoke_token(token_id: str, db: AsyncSession) -> bool:
    """Mark a token as revoked. Returns True if found."""
    try:
        tid = uuid.UUID(token_id)
    except ValueError:
        return False

    result = await db.execute(select(APIToken).where(APIToken.id == tid))
    record = result.scalar_one_or_none()
    if record is None:
        return False

    await db.execute(
        update(APIToken)
        .where(APIToken.id == tid)
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
    logger.info("token_revoked", token_id=token_id)
    return True
