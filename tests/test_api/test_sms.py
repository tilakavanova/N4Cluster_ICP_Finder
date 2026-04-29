"""Tests for SMS API router (NIF-231, NIF-233, NIF-234).

Covers:
- POST /api/v1/sms/send returns correct response
- POST /api/v1/sms/send-bulk returns summary
- POST /api/v1/sms/callback processes delivery webhook
- POST /api/v1/sms/consent records consent
- GET  /api/v1/sms/consent/{phone} returns consent status
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app


@pytest.fixture
def override_deps():
    """Override auth and DB session dependencies."""
    from src.api.auth import require_auth
    from src.db.session import get_session

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    async def _fake_session():
        yield mock_session

    async def _fake_auth():
        return "test-user"

    app.dependency_overrides[get_session] = _fake_session
    app.dependency_overrides[require_auth] = _fake_auth
    yield mock_session
    app.dependency_overrides.clear()


class TestSendSmsEndpoint:
    @pytest.mark.asyncio
    async def test_send_success(self, override_deps):
        mock_result = {"status": "sent", "message_uuid": "uuid-123", "error": None}

        with patch("src.api.routers.sms.SMSService") as MockSvc:
            instance = MockSvc.return_value
            instance.send_sms = AsyncMock(return_value=mock_result)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/api/v1/sms/send", json={
                    "to_number": "+14155551234",
                    "message": "Hello from N4Cluster!",
                    "lead_id": str(uuid.uuid4()),
                    "campaign_id": str(uuid.uuid4()),
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "sent"
        assert data["message_uuid"] == "uuid-123"

    @pytest.mark.asyncio
    async def test_send_blocked(self, override_deps):
        mock_result = {"status": "blocked", "message_uuid": None, "error": "no_consent"}

        with patch("src.api.routers.sms.SMSService") as MockSvc:
            instance = MockSvc.return_value
            instance.send_sms = AsyncMock(return_value=mock_result)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/api/v1/sms/send", json={
                    "to_number": "+14155551234",
                    "message": "Hello!",
                    "lead_id": str(uuid.uuid4()),
                    "campaign_id": str(uuid.uuid4()),
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "blocked"


class TestBulkSendEndpoint:
    @pytest.mark.asyncio
    async def test_bulk_send(self, override_deps):
        mock_result = {
            "sent": 2, "failed": 0, "blocked": 0, "rate_limited": 0,
            "details": [
                {"target_id": "t1", "status": "sent", "message_uuid": "u1", "error": None},
                {"target_id": "t2", "status": "sent", "message_uuid": "u2", "error": None},
            ],
        }

        with patch("src.api.routers.sms.SMSService") as MockSvc:
            instance = MockSvc.return_value
            instance.send_bulk_sms = AsyncMock(return_value=mock_result)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/api/v1/sms/send-bulk", json={
                    "campaign_id": str(uuid.uuid4()),
                    "message_template": "Hello {{name}}!",
                    "targets": [
                        {"target_id": str(uuid.uuid4()), "lead_id": str(uuid.uuid4()), "phone_number": "+14155551234"},
                    ],
                })

        assert resp.status_code == 200
        assert resp.json()["sent"] == 2


class TestCallbackEndpoint:
    @pytest.mark.asyncio
    async def test_callback_json(self, override_deps):
        with patch("src.api.routers.sms.handle_delivery_callback") as mock_cb:
            mock_cb.return_value = None

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post(
                    "/api/v1/sms/callback",
                    json={"MessageUUID": "uuid-1", "Status": "delivered"},
                )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_cb.assert_called_once()


class TestConsentEndpoints:
    @pytest.mark.asyncio
    async def test_record_opt_in(self, override_deps):
        mock_consent = MagicMock()
        mock_consent.phone_number = "+14155551234"
        mock_consent.consent_type = "opt_in"
        mock_consent.is_active = True
        mock_consent.source = "api"

        with patch("src.api.routers.sms.record_consent", return_value=mock_consent):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/api/v1/sms/consent", json={
                    "phone_number": "+14155551234",
                    "consent_type": "opt_in",
                    "source": "api",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["consent_type"] == "opt_in"
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_record_opt_out(self, override_deps):
        mock_consent = MagicMock()
        mock_consent.phone_number = "+14155551234"
        mock_consent.consent_type = "opt_out"
        mock_consent.is_active = False
        mock_consent.source = "sms_keyword"

        with patch("src.api.routers.sms.process_opt_out", return_value=mock_consent):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/api/v1/sms/consent", json={
                    "phone_number": "+14155551234",
                    "consent_type": "opt_out",
                })

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    @pytest.mark.asyncio
    async def test_get_consent_status(self, override_deps):
        with patch("src.api.routers.sms.check_consent", return_value=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/v1/sms/consent/+14155551234")

        assert resp.status_code == 200
        assert resp.json()["consent_type"] == "opt_in"

    @pytest.mark.asyncio
    async def test_get_consent_status_no_consent(self, override_deps):
        with patch("src.api.routers.sms.check_consent", return_value=False):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/v1/sms/consent/+14155551234")

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
