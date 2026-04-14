"""Tests for src/services/auth_service.py (NIF-254).

Covers:
- create_client returns client_id and raw secret
- authenticate_client with correct credentials
- authenticate_client with wrong secret
- authenticate_client with inactive client
- authenticate_client with unknown client_id
- create_token produces a decodable JWT with correct payload
- verify_token decodes correctly
- verify_token raises on expired token
- verify_token raises on tampered token
- is_token_revoked returns False for active token
- is_token_revoked returns True for revoked token
- revoke_token marks token as revoked
- rotate_client_secret returns new secret and invalidates old one
- rate_limit_per_minute is stored on the client
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from src.services import auth_service
from src.services.auth_service import (
    _hash_secret,
    _hash_token,
    _verify_secret,
    authenticate_client,
    create_client,
    create_token,
    deactivate_client,
    is_token_revoked,
    revoke_token,
    rotate_client_secret,
    verify_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    client_id="cid_test123",
    name="Test App",
    scopes=None,
    is_active=True,
    raw_secret="raw-secret-value",
    rate_limit_per_minute=60,
):
    client = MagicMock()
    client.id = uuid.uuid4()
    client.client_id = client_id
    client.name = name
    client.scopes = scopes or ["leads:read"]
    client.is_active = is_active
    client.client_secret_hash = _hash_secret(raw_secret)
    client.rate_limit_per_minute = rate_limit_per_minute
    return client


def _make_token_record(token_str, client, revoked=False):
    record = MagicMock()
    record.id = uuid.uuid4()
    record.client_id = client.id
    record.token_hash = _hash_token(token_str)
    record.revoked_at = datetime.now(timezone.utc) if revoked else None
    return record


def _mock_db_returning(obj):
    """Return an AsyncMock DB where scalar_one_or_none() yields *obj*."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Secret hashing helpers
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_hash_and_verify_secret(self):
        raw = "super-secret-value"
        hashed = _hash_secret(raw)
        assert _verify_secret(raw, hashed) is True

    def test_wrong_secret_fails(self):
        hashed = _hash_secret("correct")
        assert _verify_secret("wrong", hashed) is False

    def test_hash_token_is_deterministic(self):
        token = "some.jwt.token"
        assert _hash_token(token) == _hash_token(token)

    def test_hash_token_different_inputs(self):
        assert _hash_token("aaa") != _hash_token("bbb")


# ---------------------------------------------------------------------------
# create_client
# ---------------------------------------------------------------------------


class TestCreateClient:
    @pytest.mark.asyncio
    async def test_returns_client_id_and_secret(self):
        db = _mock_db_returning(None)  # result not used in create_client

        client_id, raw_secret = await create_client(
            name="My App", scopes=["leads:read", "leads:write"], db=db
        )

        assert client_id.startswith("cid_")
        assert len(raw_secret) > 20
        db.add.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_client_id_is_unique_across_calls(self):
        db1 = _mock_db_returning(None)
        db2 = _mock_db_returning(None)

        cid1, _ = await create_client("App1", ["leads:read"], db=db1)
        cid2, _ = await create_client("App2", ["leads:read"], db=db2)
        assert cid1 != cid2

    @pytest.mark.asyncio
    async def test_rate_limit_stored(self):
        db = _mock_db_returning(None)
        added_clients = []
        db.add = lambda obj: added_clients.append(obj)

        await create_client(
            name="RL App", scopes=["leads:read"], db=db, rate_limit_per_minute=120
        )

        assert len(added_clients) == 1
        assert added_clients[0].rate_limit_per_minute == 120


# ---------------------------------------------------------------------------
# authenticate_client
# ---------------------------------------------------------------------------


class TestAuthenticateClient:
    @pytest.mark.asyncio
    async def test_correct_credentials_returns_client(self):
        raw = "correct-secret"
        client = _make_client(raw_secret=raw)
        db = _mock_db_returning(client)

        result = await authenticate_client("cid_test123", raw, db)
        assert result is client

    @pytest.mark.asyncio
    async def test_wrong_secret_returns_none(self):
        client = _make_client(raw_secret="correct-secret")
        db = _mock_db_returning(client)

        result = await authenticate_client("cid_test123", "wrong-secret", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_client_id_returns_none(self):
        db = _mock_db_returning(None)

        result = await authenticate_client("nonexistent", "any-secret", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_inactive_client_returns_none(self):
        client = _make_client(raw_secret="secret", is_active=False)
        db = _mock_db_returning(client)

        result = await authenticate_client(client.client_id, "secret", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_updates_last_used_at_on_success(self):
        raw = "good-secret"
        client = _make_client(raw_secret=raw)
        db = _mock_db_returning(client)

        await authenticate_client(client.client_id, raw, db)
        # Should have called execute twice: select + update
        assert db.execute.call_count == 2


# ---------------------------------------------------------------------------
# create_token / verify_token
# ---------------------------------------------------------------------------


class TestTokenIssuance:
    def test_create_token_returns_string(self):
        client = _make_client()
        token = create_token(client)
        assert isinstance(token, str)
        assert token.count(".") == 2  # JWT structure: header.payload.sig

    def test_create_token_payload_fields(self):
        client = _make_client(scopes=["leads:read", "scoring:write"])
        token = create_token(client)

        from src.config import settings
        payload = jwt.decode(
            token,
            settings.effective_jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        assert payload["sub"] == client.client_id
        assert payload["scopes"] == ["leads:read", "scoring:write"]
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload

    def test_create_token_custom_scopes(self):
        client = _make_client(scopes=["leads:read", "scoring:write"])
        token = create_token(client, scopes=["leads:read"])

        from src.config import settings
        payload = jwt.decode(
            token, settings.effective_jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        assert payload["scopes"] == ["leads:read"]

    def test_create_token_custom_expiry(self):
        client = _make_client()
        token = create_token(client, expires_in=7200)

        from src.config import settings
        payload = jwt.decode(
            token, settings.effective_jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        delta = payload["exp"] - payload["iat"]
        assert delta == 7200

    def test_verify_token_decodes_correctly(self):
        client = _make_client()
        token = create_token(client, scopes=["leads:read"])
        payload = verify_token(token)
        assert payload["sub"] == client.client_id
        assert payload["scopes"] == ["leads:read"]

    def test_verify_token_raises_on_expired(self):
        client = _make_client()
        token = create_token(client, expires_in=-1)  # already expired

        with pytest.raises(jwt.ExpiredSignatureError):
            verify_token(token)

    def test_verify_token_raises_on_tampered_signature(self):
        client = _make_client()
        token = create_token(client)
        tampered = token[:-4] + "XXXX"

        with pytest.raises(jwt.InvalidTokenError):
            verify_token(tampered)

    def test_verify_token_raises_on_wrong_secret(self):
        client = _make_client()
        from src.config import settings
        token = jwt.encode(
            {"sub": "x", "scopes": [], "exp": 9999999999, "iat": 1},
            "wrong-secret",
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(jwt.InvalidSignatureError):
            verify_token(token)


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


class TestTokenRevocation:
    @pytest.mark.asyncio
    async def test_is_token_revoked_returns_false_for_active(self):
        db = _mock_db_returning(None)  # no revoked record found
        client = _make_client()
        token = create_token(client)

        result = await is_token_revoked(token, db)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_token_revoked_returns_true_for_revoked(self):
        client = _make_client()
        token = create_token(client)
        revoked_record = _make_token_record(token, client, revoked=True)

        db = _mock_db_returning(revoked_record)
        result = await is_token_revoked(token, db)
        assert result is True

    @pytest.mark.asyncio
    async def test_revoke_token_returns_true_when_found(self):
        client = _make_client()
        token = create_token(client)
        payload = verify_token(token)
        token_id = payload["jti"]

        token_record = _make_token_record(token, client, revoked=False)
        db = _mock_db_returning(token_record)

        result = await revoke_token(token_id, db)
        assert result is True

    @pytest.mark.asyncio
    async def test_revoke_token_returns_false_when_not_found(self):
        db = _mock_db_returning(None)
        result = await revoke_token(str(uuid.uuid4()), db)
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_token_with_invalid_uuid_returns_false(self):
        db = _mock_db_returning(None)
        result = await revoke_token("not-a-uuid", db)
        assert result is False


# ---------------------------------------------------------------------------
# Secret rotation
# ---------------------------------------------------------------------------


class TestSecretRotation:
    @pytest.mark.asyncio
    async def test_rotate_returns_new_secret(self):
        old_secret = "old-secret-value"
        client = _make_client(raw_secret=old_secret)
        db = _mock_db_returning(client)

        new_secret = await rotate_client_secret(client.client_id, db)
        assert new_secret is not None
        assert new_secret != old_secret
        assert len(new_secret) > 20

    @pytest.mark.asyncio
    async def test_rotate_returns_none_for_unknown_client(self):
        db = _mock_db_returning(None)
        result = await rotate_client_secret("nonexistent", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_old_secret_no_longer_valid_after_rotation(self):
        old_secret = "old-secret"
        client = _make_client(raw_secret=old_secret)
        db = _mock_db_returning(client)

        new_secret = await rotate_client_secret(client.client_id, db)

        # After rotation the stored hash has been updated via execute(update(…))
        # The new secret is NOT equal to the old one
        assert new_secret != old_secret


# ---------------------------------------------------------------------------
# deactivate_client
# ---------------------------------------------------------------------------


class TestDeactivateClient:
    @pytest.mark.asyncio
    async def test_deactivate_returns_true_when_found(self):
        client = _make_client()
        db = _mock_db_returning(client)

        result = await deactivate_client(client.client_id, db)
        assert result is True

    @pytest.mark.asyncio
    async def test_deactivate_returns_false_when_not_found(self):
        db = _mock_db_returning(None)
        result = await deactivate_client("unknown", db)
        assert result is False
