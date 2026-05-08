"""Tests for campaign anomaly detection service (NIF-267).

Covers:
- check_campaign_health detects high bounce rate
- check_campaign_health detects high complaint rate
- check_campaign_health detects volume drop
- check_campaign_health returns empty when healthy
- auto_pause_campaign pauses active campaign
- auto_pause_campaign skips non-active campaign
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.anomaly_detection import (
    check_campaign_health,
    auto_pause_campaign,
    check_and_pause_if_needed,
    BOUNCE_RATE_THRESHOLD,
    COMPLAINT_RATE_THRESHOLD,
    VOLUME_DROP_THRESHOLD,
)


def _mock_session_with_metrics(total_sends, bounces, complaints, prev_volume=0):
    """Build a mock session that returns given metric counts."""
    session = AsyncMock()
    call_count = [0]

    async def mock_execute(query):
        result = MagicMock()
        idx = call_count[0]
        call_count[0] += 1

        # Order of queries: sends, bounces, complaints, prev_volume
        values = [total_sends, bounces, complaints, prev_volume]
        result.scalar.return_value = values[idx] if idx < len(values) else 0
        return result

    session.execute = mock_execute
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


class TestCheckCampaignHealth:
    @pytest.mark.asyncio
    async def test_healthy_campaign(self):
        """No anomalies when metrics are within thresholds."""
        session = _mock_session_with_metrics(
            total_sends=100, bounces=5, complaints=0, prev_volume=90,
        )
        campaign_id = uuid.uuid4()
        anomalies = await check_campaign_health(session, campaign_id)
        assert len(anomalies) == 0

    @pytest.mark.asyncio
    async def test_high_bounce_rate(self):
        """Detects bounce rate above threshold."""
        session = _mock_session_with_metrics(
            total_sends=100, bounces=15, complaints=0, prev_volume=100,
        )
        campaign_id = uuid.uuid4()
        anomalies = await check_campaign_health(session, campaign_id)
        bounce_anomalies = [a for a in anomalies if a["type"] == "high_bounce"]
        assert len(bounce_anomalies) == 1
        assert bounce_anomalies[0]["metric_value"] > BOUNCE_RATE_THRESHOLD

    @pytest.mark.asyncio
    async def test_high_complaint_rate(self):
        """Detects complaint rate above threshold."""
        session = _mock_session_with_metrics(
            total_sends=100, bounces=0, complaints=5, prev_volume=100,
        )
        campaign_id = uuid.uuid4()
        anomalies = await check_campaign_health(session, campaign_id)
        complaint_anomalies = [a for a in anomalies if a["type"] == "high_complaint"]
        assert len(complaint_anomalies) == 1
        assert complaint_anomalies[0]["metric_value"] > COMPLAINT_RATE_THRESHOLD

    @pytest.mark.asyncio
    async def test_volume_drop(self):
        """Detects volume drop above threshold."""
        session = _mock_session_with_metrics(
            total_sends=20, bounces=0, complaints=0, prev_volume=100,
        )
        campaign_id = uuid.uuid4()
        anomalies = await check_campaign_health(session, campaign_id)
        drop_anomalies = [a for a in anomalies if a["type"] == "volume_drop"]
        assert len(drop_anomalies) == 1
        assert drop_anomalies[0]["metric_value"] > VOLUME_DROP_THRESHOLD

    @pytest.mark.asyncio
    async def test_no_sends_no_anomaly(self):
        """No anomalies when there are zero sends."""
        session = _mock_session_with_metrics(
            total_sends=0, bounces=0, complaints=0, prev_volume=0,
        )
        campaign_id = uuid.uuid4()
        anomalies = await check_campaign_health(session, campaign_id)
        assert len(anomalies) == 0


class TestAutoPauseCampaign:
    @pytest.mark.asyncio
    async def test_pauses_active_campaign(self):
        """Auto-pause sets campaign status to paused."""
        campaign = MagicMock()
        campaign.status = "active"
        session = AsyncMock()
        session.get = AsyncMock(return_value=campaign)
        session.flush = AsyncMock()

        result = await auto_pause_campaign(session, uuid.uuid4(), "high_bounce")
        assert campaign.status == "paused"

    @pytest.mark.asyncio
    async def test_skips_non_active(self):
        """Doesn't pause a campaign that's already paused."""
        campaign = MagicMock()
        campaign.status = "paused"
        session = AsyncMock()
        session.get = AsyncMock(return_value=campaign)

        result = await auto_pause_campaign(session, uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_missing_campaign(self):
        """Returns None for non-existent campaign."""
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)

        result = await auto_pause_campaign(session, uuid.uuid4())
        assert result is None
