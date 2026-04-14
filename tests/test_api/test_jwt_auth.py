"""Tests for JWT/OAuth2 auth dependencies and endpoints (NIF-254).

Covers:
- POST /api/v1/auth/token with valid credentials returns JWT
- POST /api/v1/auth/token with invalid credentials returns 401
- Legacy X-API-Key still works on protected endpoints (backward compat)
- Bearer token auth works on existing endpoints
- Missing auth returns 401
- Scope enforcement: has scope passes, missing scope returns 403
- require_auth returns dev-mode when api_key is empty
- require_auth accepts legacy X-API-Key
- require_auth accepts valid Bearer token
- require_auth rejects expired Bearer token
- require_auth rejects invalid Bearer token
- require_scope passes for correct scope
- require_scope passes for admin:all (super-scope)
- require_scope blocks missing scope
- POST /auth/clients creates new client (admin:all)
- DELETE /auth/clients/{id} deactivates client (admin:all)
- POST /auth/clients/{id}/rotate rotates secret (admin:all)
- POST /auth/token/refresh issues new token
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.api.auth import require_auth, require_scope
from src.services.auth_service import (
    _hash_secret,
    create_token,
    verify_token,
    VALID_SCOPES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    client_id="cid_test",
    scopes=None,
    is_active=True,
    raw_secret="test-secret",
    rate_limit_per_minute=60,
):
    client = MagicMock()
    client.id = uuid.uuid4()
    client.client_id = client_id
    client.scopes = scopes or ["leads:read"]
    client.is_active = is_active
    client.client_secret_hash = _hash_secret(raw_secret)
    client.rate_limit_per_minute = rate_limit_per_minute
    return client


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="bearer", credentials=token)


# ---------------------------------------------------------------------------
# require_auth unit tests
# ---------------------------------------------------------------------------


class TestRequireAuth:
    @pytest.mark.asyncio
    async def test_dev_mode_when_no_api_key_configured(self):
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            mock_settings.effective_jwt_secret = ""
            result = await require_auth(api_key=None, credentials=None)
        assert result["mode"] == "dev"

    @pytest.mark.asyncio
    async def test_legacy_api_key_accepted(self):
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "secret-key"
            mock_settings.effective_jwt_secret = "jwt-secret"
            result = await require_auth(api_key="secret-key", credentials=None)
        assert result["mode"] == "legacy"
        assert "admin:all" in result["scopes"]

    @pytest.mark.asyncio
    async def test_wrong_legacy_key_falls_through_to_bearer_check(self):
        """Wrong X-API-Key + no Bearer → 401."""
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "correct-key"
            mock_settings.effective_jwt_secret = "x"
            with pytest.raises(HTTPException) as exc:
                await require_auth(api_key="wrong-key", credentials=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_auth_raises_401(self):
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "some-key"
            mock_settings.effective_jwt_secret = "x"
            with pytest.raises(HTTPException) as exc:
                await require_auth(api_key=None, credentials=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_bearer_token_accepted(self):
        client = _make_client(scopes=["leads:read", "scoring:read"])
        token = create_token(client)
        creds = _bearer(token)

        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "some-key"
            mock_settings.effective_jwt_secret = __import__(
                "src.config", fromlist=["settings"]
            ).settings.effective_jwt_secret
            mock_settings.jwt_algorithm = "HS256"
            # Use real verify_token (patching settings inside auth_service)
            result = await require_auth(api_key=None, credentials=creds)

        assert result["mode"] == "jwt"
        assert result["sub"] == client.client_id

    @pytest.mark.asyncio
    async def test_expired_bearer_token_raises_401(self):
        client = _make_client()
        token = create_token(client, expires_in=-1)
        creds = _bearer(token)

        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "key"
            mock_settings.effective_jwt_secret = __import__(
                "src.config", fromlist=["settings"]
            ).settings.effective_jwt_secret
            mock_settings.jwt_algorithm = "HS256"
            with pytest.raises(HTTPException) as exc:
                await require_auth(api_key=None, credentials=creds)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_invalid_bearer_token_raises_401(self):
        creds = _bearer("not.a.valid.jwt")

        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "key"
            mock_settings.effective_jwt_secret = "secret"
            mock_settings.jwt_algorithm = "HS256"
            with pytest.raises(HTTPException) as exc:
                await require_auth(api_key=None, credentials=creds)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# require_scope unit tests
# ---------------------------------------------------------------------------


class TestRequireScope:
    @pytest.mark.asyncio
    async def test_scope_present_passes(self):
        auth_ctx = {"mode": "jwt", "sub": "cid_x", "scopes": ["leads:read", "scoring:read"]}
        checker = require_scope("leads:read")
        with patch("src.api.auth.require_auth", return_value=auth_ctx):
            result = await checker(auth=auth_ctx)
        assert result is auth_ctx

    @pytest.mark.asyncio
    async def test_admin_all_grants_any_scope(self):
        auth_ctx = {"mode": "jwt", "sub": "cid_x", "scopes": ["admin:all"]}
        checker = require_scope("leads:write")
        result = await checker(auth=auth_ctx)
        assert result is auth_ctx

    @pytest.mark.asyncio
    async def test_missing_scope_raises_403(self):
        auth_ctx = {"mode": "jwt", "sub": "cid_x", "scopes": ["leads:read"]}
        checker = require_scope("scoring:write")
        with pytest.raises(HTTPException) as exc:
            await checker(auth=auth_ctx)
        assert exc.value.status_code == 403
        assert "scoring:write" in exc.value.detail

    @pytest.mark.asyncio
    async def test_legacy_mode_passes_all_scopes(self):
        auth_ctx = {"mode": "legacy", "sub": "legacy", "scopes": list(VALID_SCOPES)}
        checker = require_scope("crawl:execute")
        result = await checker(auth=auth_ctx)
        assert result is auth_ctx

    @pytest.mark.asyncio
    async def test_dev_mode_passes_all_scopes(self):
        auth_ctx = {"mode": "dev", "sub": "dev-mode", "scopes": list(VALID_SCOPES)}
        checker = require_scope("outreach:execute")
        result = await checker(auth=auth_ctx)
        assert result is auth_ctx


# ---------------------------------------------------------------------------
# POST /api/v1/auth/token endpoint
# ---------------------------------------------------------------------------


class TestTokenEndpoint:
    @pytest.mark.asyncio
    async def test_valid_credentials_return_jwt(self):
        from src.api.routers.auth import issue_token

        client = _make_client(raw_secret="my-secret", scopes=["leads:read"])

        mock_db = AsyncMock()
        with patch("src.api.routers.auth.auth_service.authenticate_client", new=AsyncMock(return_value=client)), \
             patch("src.api.routers.auth.auth_service.persist_token", new=AsyncMock()):
            response = await issue_token(
                client_id=client.client_id,
                client_secret="my-secret",
                scope="",
                db=mock_db,
            )

        assert "access_token" in response
        assert response["token_type"] == "bearer"
        assert response["expires_in"] == 3600
        payload = verify_token(response["access_token"])
        assert payload["sub"] == client.client_id

    @pytest.mark.asyncio
    async def test_invalid_credentials_return_401(self):
        from src.api.routers.auth import issue_token

        mock_db = AsyncMock()
        with patch(
            "src.api.routers.auth.auth_service.authenticate_client",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc:
                await issue_token(
                    client_id="bad-id",
                    client_secret="bad-secret",
                    scope="",
                    db=mock_db,
                )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_scope_filtering(self):
        """Requested scopes are intersected with client's allowed scopes."""
        from src.api.routers.auth import issue_token

        client = _make_client(scopes=["leads:read"])
        mock_db = AsyncMock()
        with patch(
            "src.api.routers.auth.auth_service.authenticate_client",
            new=AsyncMock(return_value=client),
        ), patch("src.api.routers.auth.auth_service.persist_token", new=AsyncMock()):
            response = await issue_token(
                client_id=client.client_id,
                client_secret="test-secret",
                scope="leads:read scoring:write",  # scoring:write not allowed
                db=mock_db,
            )

        assert "leads:read" in response["scope"]
        assert "scoring:write" not in response["scope"]


# ---------------------------------------------------------------------------
# POST /api/v1/auth/token/refresh endpoint
# ---------------------------------------------------------------------------


class TestRefreshTokenEndpoint:
    @pytest.mark.asyncio
    async def test_refresh_issues_new_token(self):
        from src.api.routers.auth import refresh_token
        from src.db.auth_models import APIClient

        client = _make_client(scopes=["leads:read"])
        auth_ctx = {
            "mode": "jwt",
            "sub": client.client_id,
            "scopes": ["leads:read"],
        }

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = client
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=result_mock)

        with patch("src.api.routers.auth.auth_service.persist_token", new=AsyncMock()):
            response = await refresh_token(auth=auth_ctx, db=mock_db)

        assert "access_token" in response
        assert response["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_refresh_rejects_legacy_mode(self):
        from src.api.routers.auth import refresh_token

        auth_ctx = {"mode": "legacy", "sub": "legacy", "scopes": list(VALID_SCOPES)}
        mock_db = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await refresh_token(auth=auth_ctx, db=mock_db)
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/v1/auth/clients endpoint
# ---------------------------------------------------------------------------


class TestCreateClientEndpoint:
    @pytest.mark.asyncio
    async def test_admin_can_create_client(self):
        from src.api.routers.auth import create_client

        admin_auth = {"mode": "jwt", "sub": "admin", "scopes": ["admin:all"]}
        mock_db = AsyncMock()

        with patch(
            "src.api.routers.auth.auth_service.create_client",
            new=AsyncMock(return_value=("cid_new", "raw-secret-value")),
        ):
            response = await create_client(
                name="New App",
                scopes="leads:read leads:write",
                rate_limit_per_minute=30,
                _auth=admin_auth,
                db=mock_db,
            )

        assert response["client_id"] == "cid_new"
        assert response["client_secret"] == "raw-secret-value"
        assert "leads:read" in response["scopes"]

    @pytest.mark.asyncio
    async def test_invalid_scope_returns_422(self):
        from src.api.routers.auth import create_client

        admin_auth = {"mode": "jwt", "sub": "admin", "scopes": ["admin:all"]}
        mock_db = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await create_client(
                name="Bad App",
                scopes="invalid:scope",
                rate_limit_per_minute=60,
                _auth=admin_auth,
                db=mock_db,
            )
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/v1/auth/clients/{id} endpoint
# ---------------------------------------------------------------------------


class TestDeactivateClientEndpoint:
    @pytest.mark.asyncio
    async def test_admin_can_deactivate(self):
        from src.api.routers.auth import deactivate_client

        admin_auth = {"mode": "jwt", "sub": "admin", "scopes": ["admin:all"]}
        mock_db = AsyncMock()

        with patch(
            "src.api.routers.auth.auth_service.deactivate_client",
            new=AsyncMock(return_value=True),
        ):
            response = await deactivate_client(
                client_id="cid_old", _auth=admin_auth, db=mock_db
            )
        assert response["client_id"] == "cid_old"

    @pytest.mark.asyncio
    async def test_unknown_client_returns_404(self):
        from src.api.routers.auth import deactivate_client

        admin_auth = {"mode": "jwt", "sub": "admin", "scopes": ["admin:all"]}
        mock_db = AsyncMock()

        with patch(
            "src.api.routers.auth.auth_service.deactivate_client",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(HTTPException) as exc:
                await deactivate_client(
                    client_id="nonexistent", _auth=admin_auth, db=mock_db
                )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/auth/clients/{id}/rotate endpoint
# ---------------------------------------------------------------------------


class TestRotateSecretEndpoint:
    @pytest.mark.asyncio
    async def test_admin_can_rotate(self):
        from src.api.routers.auth import rotate_secret

        admin_auth = {"mode": "jwt", "sub": "admin", "scopes": ["admin:all"]}
        mock_db = AsyncMock()

        with patch(
            "src.api.routers.auth.auth_service.rotate_client_secret",
            new=AsyncMock(return_value="brand-new-secret"),
        ):
            response = await rotate_secret(
                client_id="cid_x", _auth=admin_auth, db=mock_db
            )
        assert response["client_secret"] == "brand-new-secret"
        assert response["client_id"] == "cid_x"

    @pytest.mark.asyncio
    async def test_unknown_client_returns_404(self):
        from src.api.routers.auth import rotate_secret

        admin_auth = {"mode": "jwt", "sub": "admin", "scopes": ["admin:all"]}
        mock_db = AsyncMock()

        with patch(
            "src.api.routers.auth.auth_service.rotate_client_secret",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc:
                await rotate_secret(
                    client_id="ghost", _auth=admin_auth, db=mock_db
                )
        assert exc.value.status_code == 404
