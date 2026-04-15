"""Tests for GDPR data erasure endpoint (NIF-256).

Covers:
- Successful erasure redacts all PII fields on the Lead
- Audit log entry is created with correct details
- Related OutreachTargets and OutreachActivities are deleted
- Related TrackerEvents are deleted
- Requires admin:all scope (403 for non-admin)
- Returns 404 for non-existent lead
- Return body contains deletion summary
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from fastapi import HTTPException

from src.api.routers.leads import erase_lead_pii


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lead(lead_id=None):
    lead = MagicMock()
    lead.id = lead_id or uuid.uuid4()
    lead.first_name = "John"
    lead.last_name = "Doe"
    lead.email = "john@example.com"
    lead.company = "Joe's Pizza"
    lead.message = "Interested in switching"
    lead.utm_source = "google"
    lead.utm_medium = "cpc"
    lead.utm_campaign = "promo"
    lead.hubspot_contact_id = "hs-123"
    lead.hubspot_deal_id = "deal-456"
    return lead


def _make_target(lead_id):
    t = MagicMock()
    t.id = uuid.uuid4()
    t.lead_id = lead_id
    return t


def _make_admin_auth():
    return {"mode": "jwt", "sub": "admin_user", "scopes": ["admin:all"]}


# ---------------------------------------------------------------------------
# Core erasure tests
# ---------------------------------------------------------------------------


class TestEraseLeadPII:
    @pytest.mark.asyncio
    async def test_successful_erasure_redacts_pii(self):
        lead_id = uuid.uuid4()
        lead = _make_lead(lead_id)
        target = _make_target(lead_id)

        mock_session = AsyncMock()

        # Sequence of execute() calls:
        # 1. SELECT Lead
        # 2. SELECT OutreachTarget
        # 3. DELETE OutreachActivity  (returning)
        # 4. DELETE OutreachTarget    (returning)
        # 5. DELETE TrackerEvent      (returning)

        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = lead

        target_result = MagicMock()
        target_result.scalars.return_value.all.return_value = [target]

        # DELETE … RETURNING results (list of tuples)
        del_activity_result = MagicMock()
        del_activity_result.all.return_value = [("act-1",), ("act-2",)]

        del_target_result = MagicMock()
        del_target_result.all.return_value = [("tgt-1",)]

        del_tracker_result = MagicMock()
        del_tracker_result.all.return_value = [("te-1",)]

        mock_session.execute = AsyncMock(
            side_effect=[
                lead_result,
                target_result,
                del_activity_result,
                del_target_result,
                del_tracker_result,
            ]
        )

        with patch("src.api.routers.leads.AuditLog") as MockAuditLog:
            result = await erase_lead_pii(
                lead_id=lead_id,
                auth=_make_admin_auth(),
                session=mock_session,
            )

        # PII fields must be redacted
        assert lead.first_name == "[REDACTED]"
        assert lead.last_name == "[REDACTED]"
        assert lead.email == "[REDACTED]"
        assert lead.company == "[REDACTED]"
        assert lead.message == "[REDACTED]"
        assert lead.utm_source is None
        assert lead.utm_medium is None
        assert lead.utm_campaign is None
        assert lead.hubspot_contact_id is None
        assert lead.hubspot_deal_id is None

        # Return body summary
        assert result["lead_id"] == str(lead_id)
        assert result["pii_redacted"] is True
        assert result["targets_deleted"] == 1
        assert result["activities_deleted"] == 2
        assert result["tracker_events_deleted"] == 1

    @pytest.mark.asyncio
    async def test_audit_log_created(self):
        lead_id = uuid.uuid4()
        lead = _make_lead(lead_id)

        mock_session = AsyncMock()

        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = lead

        target_result = MagicMock()
        target_result.scalars.return_value.all.return_value = []

        empty = MagicMock()
        empty.all.return_value = []

        mock_session.execute = AsyncMock(
            side_effect=[lead_result, target_result, empty]
        )

        captured_audit = {}

        def capture_audit(**kwargs):
            captured_audit.update(kwargs)
            return MagicMock()

        with patch("src.api.routers.leads.AuditLog", side_effect=capture_audit):
            await erase_lead_pii(
                lead_id=lead_id,
                auth=_make_admin_auth(),
                session=mock_session,
            )

        assert captured_audit["action"] == "gdpr_erasure"
        assert captured_audit["entity_type"] == "lead"
        assert captured_audit["performed_by"] == "admin_user"
        assert captured_audit["details"]["lead_id"] == str(lead_id)
        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_nonexistent_lead_returns_404(self):
        lead_id = uuid.uuid4()
        mock_session = AsyncMock()

        not_found = MagicMock()
        not_found.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=not_found)

        with pytest.raises(HTTPException) as exc:
            await erase_lead_pii(
                lead_id=lead_id,
                auth=_make_admin_auth(),
                session=mock_session,
            )

        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_no_related_records_still_succeeds(self):
        """Lead with no outreach/tracking records erases cleanly."""
        lead_id = uuid.uuid4()
        lead = _make_lead(lead_id)

        mock_session = AsyncMock()

        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = lead

        empty_targets = MagicMock()
        empty_targets.scalars.return_value.all.return_value = []

        empty_tracker = MagicMock()
        empty_tracker.all.return_value = []

        mock_session.execute = AsyncMock(
            side_effect=[lead_result, empty_targets, empty_tracker]
        )

        with patch("src.api.routers.leads.AuditLog"):
            result = await erase_lead_pii(
                lead_id=lead_id,
                auth=_make_admin_auth(),
                session=mock_session,
            )

        assert result["targets_deleted"] == 0
        assert result["activities_deleted"] == 0
        assert result["tracker_events_deleted"] == 0
        assert result["pii_redacted"] is True


# ---------------------------------------------------------------------------
# Scope enforcement tests
# ---------------------------------------------------------------------------


class TestErasureRequiresAdminScope:
    @pytest.mark.asyncio
    async def test_missing_scope_raises_403(self):
        """require_scope('admin:all') on the router raises 403 for non-admin."""
        from src.api.auth import require_scope

        non_admin_auth = {"mode": "jwt", "sub": "cid_x", "scopes": ["leads:read"]}
        checker = require_scope("admin:all")
        with pytest.raises(HTTPException) as exc:
            await checker(auth=non_admin_auth)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_scope_passes(self):
        from src.api.auth import require_scope

        admin_auth = {"mode": "jwt", "sub": "admin", "scopes": ["admin:all"]}
        checker = require_scope("admin:all")
        result = await checker(auth=admin_auth)
        assert result is admin_auth
