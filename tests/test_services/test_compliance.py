"""Tests for GDPR compliance service (NIF-241).

Covers:
- export_lead_data returns all PII fields
- export_lead_data returns empty dict for missing lead
- erase_lead_data redacts PII and cleans related data
- erase_lead_data calls HubSpot delete
- erase_lead_data returns error for missing lead
- record_consent creates audit log entry
- record_consent returns error for missing lead
- get_consent_status returns latest consent per scope
- cleanup_expired_data removes old records
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import compliance


def _make_lead(**overrides):
    lead = MagicMock()
    lead.id = overrides.get("id", uuid.uuid4())
    lead.first_name = overrides.get("first_name", "Joe")
    lead.last_name = overrides.get("last_name", "Pizza")
    lead.email = overrides.get("email", "joe@pizza.com")
    lead.company = overrides.get("company", "Joe's Pizza")
    lead.business_type = overrides.get("business_type", "restaurant")
    lead.locations = overrides.get("locations", "NYC")
    lead.interest = overrides.get("interest", "pos_upgrade")
    lead.message = overrides.get("message", "Interested in demo")
    lead.source = overrides.get("source", "website_demo")
    lead.status = overrides.get("status", "new")
    lead.lifecycle_stage = overrides.get("lifecycle_stage", "new")
    lead.owner = overrides.get("owner", None)
    lead.icp_fit_label = overrides.get("icp_fit_label", "good")
    lead.icp_total_score = overrides.get("icp_total_score", 65.0)
    lead.utm_source = overrides.get("utm_source", "google")
    lead.utm_medium = overrides.get("utm_medium", "cpc")
    lead.utm_campaign = overrides.get("utm_campaign", "spring2026")
    lead.hubspot_contact_id = overrides.get("hubspot_contact_id", "hs123")
    lead.hubspot_deal_id = overrides.get("hubspot_deal_id", "hs456")
    lead.created_at = overrides.get("created_at", datetime.now(timezone.utc))
    lead.updated_at = overrides.get("updated_at", datetime.now(timezone.utc))
    return lead


class TestExportLeadData:
    @pytest.mark.asyncio
    async def test_exports_all_fields(self):
        lead = _make_lead()
        session = AsyncMock()

        # Mock lead query
        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = lead

        # Mock empty related queries
        empty_result = MagicMock()
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        empty_result.scalars.return_value = empty_scalars

        session.execute = AsyncMock(side_effect=[lead_result] + [empty_result] * 5)
        session.add = MagicMock()
        session.flush = AsyncMock()

        result = await compliance.export_lead_data(session, lead.id)

        assert result["lead_id"] == str(lead.id)
        assert result["personal_data"]["email"] == "joe@pizza.com"
        assert result["personal_data"]["first_name"] == "Joe"
        assert result["tracking"]["hubspot_contact_id"] == "hs123"
        assert "exported_at" in result

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_lead(self):
        session = AsyncMock()
        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=lead_result)

        result = await compliance.export_lead_data(session, uuid.uuid4())
        assert result == {}


class TestEraseLeadData:
    @pytest.mark.asyncio
    async def test_redacts_pii(self):
        lead = _make_lead()
        session = AsyncMock()

        # Mock lead query
        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = lead

        # Mock empty target/activity queries
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        empty_result = MagicMock()
        empty_result.scalars.return_value = empty_scalars
        empty_result.all.return_value = []

        session.execute = AsyncMock(side_effect=[lead_result] + [empty_result] * 7)
        session.add = MagicMock()
        session.flush = AsyncMock()

        with patch("src.services.compliance.HubSpotService") as mock_hs_cls:
            mock_hs = MagicMock()
            mock_hs.delete_contact = AsyncMock(return_value=True)
            mock_hs_cls.return_value = mock_hs

            result = await compliance.erase_lead_data(session, lead.id, "admin@test.com")

        assert result["pii_redacted"] is True
        assert result["hubspot_deleted"] is True
        assert lead.first_name == "[REDACTED]"
        assert lead.email == "[REDACTED]"
        assert lead.hubspot_contact_id is None

    @pytest.mark.asyncio
    async def test_returns_error_for_missing_lead(self):
        session = AsyncMock()
        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=lead_result)

        result = await compliance.erase_lead_data(session, uuid.uuid4())
        assert result["error"] == "lead_not_found"


class TestConsentManagement:
    @pytest.mark.asyncio
    async def test_record_consent(self):
        lead = _make_lead()
        session = AsyncMock()

        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = lead
        session.execute = AsyncMock(return_value=lead_result)
        session.add = MagicMock()
        session.flush = AsyncMock()

        result = await compliance.record_consent(
            session, lead.id, scope="marketing_email", granted=True, recorded_by="user@test.com"
        )

        assert result["scope"] == "marketing_email"
        assert result["granted"] is True
        assert "recorded_at" in result
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_consent_missing_lead(self):
        session = AsyncMock()
        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=lead_result)

        result = await compliance.record_consent(
            session, uuid.uuid4(), scope="marketing", granted=True
        )
        assert result["error"] == "lead_not_found"

    @pytest.mark.asyncio
    async def test_get_consent_status(self):
        lead_id = uuid.uuid4()
        session = AsyncMock()

        # Mock audit log results
        log1 = MagicMock()
        log1.details = {
            "lead_id": str(lead_id),
            "scope": "marketing_email",
            "granted": True,
            "recorded_at": "2026-04-01T00:00:00+00:00",
            "recorded_by": "admin",
        }
        log1.created_at = datetime(2026, 4, 1, tzinfo=timezone.utc)

        log2 = MagicMock()
        log2.details = {
            "lead_id": str(lead_id),
            "scope": "analytics",
            "granted": False,
            "recorded_at": "2026-04-02T00:00:00+00:00",
            "recorded_by": "admin",
        }
        log2.created_at = datetime(2026, 4, 2, tzinfo=timezone.utc)

        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [log2, log1]  # desc order
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)

        result = await compliance.get_consent_status(session, lead_id)

        assert result["lead_id"] == str(lead_id)
        assert len(result["consents"]) == 2
        scopes = {c["scope"] for c in result["consents"]}
        assert "marketing_email" in scopes
        assert "analytics" in scopes


class TestCleanupExpiredData:
    @pytest.mark.asyncio
    async def test_cleanup_returns_counts(self):
        session = AsyncMock()

        # Mock delete results for tracker, conversion, audit
        def _make_delete_result(count):
            r = MagicMock()
            r.all.return_value = [MagicMock()] * count
            return r

        session.execute = AsyncMock(side_effect=[
            _make_delete_result(5),   # tracker events
            _make_delete_result(3),   # conversion events
            _make_delete_result(10),  # audit logs
        ])

        result = await compliance.cleanup_expired_data(session, retention_days=365)

        assert result["retention_days"] == 365
        assert result["tracker_events_deleted"] == 5
        assert result["conversion_events_deleted"] == 3
        assert result["audit_logs_deleted"] == 10
