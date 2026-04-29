"""Tests for GDPR compliance API endpoints (NIF-241).

Covers:
- POST /compliance/data-export/{lead_id} returns exported data
- POST /compliance/data-export/{lead_id} returns 404 for missing lead
- DELETE /compliance/data-erase/{lead_id} redacts PII
- DELETE /compliance/data-erase/{lead_id} returns 404 for missing lead
- POST /compliance/consent/{lead_id} records consent
- GET /compliance/consent/{lead_id} returns consent status
- POST /compliance/retention-cleanup triggers cleanup
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth import require_auth
from src.main import app


def _dev_auth_override():
    return {"mode": "dev", "sub": "dev-mode", "scopes": ["admin:all"]}


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[require_auth] = _dev_auth_override
    yield
    app.dependency_overrides.pop(require_auth, None)


class TestDataExport:
    @pytest.mark.asyncio
    async def test_exports_lead_data(self):
        lead_id = uuid.uuid4()
        mock_export = {
            "lead_id": str(lead_id),
            "personal_data": {"email": "joe@pizza.com"},
            "exported_at": "2026-04-27T00:00:00+00:00",
        }

        with patch("src.api.routers.compliance.export_lead_data", new_callable=AsyncMock, return_value=mock_export):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(f"/api/v1/compliance/data-export/{lead_id}")

            assert resp.status_code == 200
            data = resp.json()
            assert data["lead_id"] == str(lead_id)

    @pytest.mark.asyncio
    async def test_export_returns_404(self):
        lead_id = uuid.uuid4()

        with patch("src.api.routers.compliance.export_lead_data", new_callable=AsyncMock, return_value={}):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(f"/api/v1/compliance/data-export/{lead_id}")

            assert resp.status_code == 404


class TestDataErase:
    @pytest.mark.asyncio
    async def test_erases_lead_data(self):
        lead_id = uuid.uuid4()
        mock_result = {
            "lead_id": str(lead_id),
            "pii_redacted": True,
            "hubspot_deleted": True,
        }

        with patch("src.api.routers.compliance.erase_lead_data", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(f"/api/v1/compliance/data-erase/{lead_id}")

            assert resp.status_code == 200
            data = resp.json()
            assert data["pii_redacted"] is True

    @pytest.mark.asyncio
    async def test_erase_returns_404(self):
        lead_id = uuid.uuid4()
        mock_result = {"error": "lead_not_found"}

        with patch("src.api.routers.compliance.erase_lead_data", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(f"/api/v1/compliance/data-erase/{lead_id}")

            assert resp.status_code == 404


class TestConsent:
    @pytest.mark.asyncio
    async def test_records_consent(self):
        lead_id = uuid.uuid4()
        mock_result = {
            "lead_id": str(lead_id),
            "scope": "marketing_email",
            "granted": True,
            "recorded_at": "2026-04-27T00:00:00+00:00",
        }

        with patch("src.api.routers.compliance.record_consent", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/v1/compliance/consent/{lead_id}",
                    json={"scope": "marketing_email", "granted": True},
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["scope"] == "marketing_email"
            assert data["granted"] is True

    @pytest.mark.asyncio
    async def test_gets_consent_status(self):
        lead_id = uuid.uuid4()
        mock_result = {
            "lead_id": str(lead_id),
            "consents": [
                {"scope": "marketing_email", "granted": True},
            ],
        }

        with patch("src.api.routers.compliance.get_consent_status", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/api/v1/compliance/consent/{lead_id}")

            assert resp.status_code == 200
            data = resp.json()
            assert len(data["consents"]) == 1


class TestRetentionCleanup:
    @pytest.mark.asyncio
    async def test_triggers_cleanup(self):
        mock_result = {
            "retention_days": 365,
            "cutoff": "2025-04-27T00:00:00+00:00",
            "tracker_events_deleted": 5,
            "conversion_events_deleted": 3,
            "audit_logs_deleted": 10,
        }

        with patch("src.api.routers.compliance.cleanup_expired_data", new_callable=AsyncMock, return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/compliance/retention-cleanup",
                    json={"retention_days": 365},
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["tracker_events_deleted"] == 5
