"""Tests for conversion feedback loop service (NIF-260).

Covers:
- analyze_conversions returns score bucket breakdown
- suggest_weight_adjustments returns signal-level suggestions
- suggest_weight_adjustments handles no conversions
- apply_adjustments updates profile weights and creates version
- apply_adjustments returns error for missing profile
- get_feedback_report combines analysis and suggestions
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import feedback_loop


def _make_profile(**overrides):
    profile = MagicMock()
    profile.id = overrides.get("id", uuid.uuid4())
    profile.name = overrides.get("name", "Default Profile")
    profile.version = overrides.get("version", 1)
    profile.is_active = overrides.get("is_active", True)
    profile.signals = overrides.get("signals", [
        {"name": "independent", "weight": 15.0, "enabled": True},
        {"name": "platform_dependency", "weight": 20.0, "enabled": True},
        {"name": "pos", "weight": 12.0, "enabled": True},
    ])
    profile.updated_at = datetime.now(timezone.utc)
    return profile


class TestAnalyzeConversions:
    @pytest.mark.asyncio
    async def test_returns_buckets(self):
        session = AsyncMock()

        # Each bucket queries 2x (discovered + converted) = 8 total queries
        counts = [
            10, 0,   # 0-25: 10 discovered, 0 converted
            20, 2,   # 25-50: 20 discovered, 2 converted
            15, 5,   # 50-75: 15 discovered, 5 converted
            8, 6,    # 75-100: 8 discovered, 6 converted
        ]
        results = []
        for c in counts:
            r = MagicMock()
            r.scalar.return_value = c
            results.append(r)

        session.execute = AsyncMock(side_effect=results)

        result = await feedback_loop.analyze_conversions(session, "2026-04")

        assert result["period"] == "2026-04"
        assert len(result["buckets"]) == 4
        assert result["buckets"][0]["score_range"] == "0-25"
        assert result["buckets"][0]["discovered"] == 10
        assert result["buckets"][0]["converted"] == 0
        assert result["buckets"][3]["score_range"] == "75-100"
        assert result["buckets"][3]["conversion_rate"] == 75.0

    @pytest.mark.asyncio
    async def test_handles_iso_week_period(self):
        session = AsyncMock()

        counts = [0, 0] * 4  # All zeros
        results = []
        for c in counts:
            r = MagicMock()
            r.scalar.return_value = c
            results.append(r)

        session.execute = AsyncMock(side_effect=results)

        result = await feedback_loop.analyze_conversions(session, "2026-W15")
        assert result["period"] == "2026-W15"
        assert len(result["buckets"]) == 4


class TestSuggestWeightAdjustments:
    @pytest.mark.asyncio
    async def test_suggests_adjustments(self):
        session = AsyncMock()
        profile = _make_profile()

        # Mock converted IDs
        converted_result = MagicMock()
        converted_result.all.return_value = [(uuid.uuid4(),), (uuid.uuid4(),)]

        # Mock discovered IDs (superset)
        discovered_result = MagicMock()
        discovered_result.all.return_value = converted_result.all() + [(uuid.uuid4(),), (uuid.uuid4(),)]

        # Mock profile query
        profile_result = MagicMock()
        profile_result.scalar_one_or_none.return_value = profile

        # Mock score explanations for converted
        conv_exp = MagicMock()
        conv_exp.signal_breakdown = [
            {"signal": "independent", "raw_value": 0.9},
            {"signal": "platform_dependency", "raw_value": 0.7},
        ]
        conv_expl_result = MagicMock()
        conv_expl_scalars = MagicMock()
        conv_expl_scalars.all.return_value = [conv_exp]
        conv_expl_result.scalars.return_value = conv_expl_scalars

        # Mock score explanations for non-converted
        nonconv_exp = MagicMock()
        nonconv_exp.signal_breakdown = [
            {"signal": "independent", "raw_value": 0.3},
            {"signal": "platform_dependency", "raw_value": 0.5},
        ]
        nonconv_expl_result = MagicMock()
        nonconv_expl_scalars = MagicMock()
        nonconv_expl_scalars.all.return_value = [nonconv_exp]
        nonconv_expl_result.scalars.return_value = nonconv_expl_scalars

        session.execute = AsyncMock(side_effect=[
            converted_result,
            discovered_result,
            profile_result,
            conv_expl_result,
            nonconv_expl_result,
        ])
        session.get = AsyncMock(return_value=None)

        result = await feedback_loop.suggest_weight_adjustments(session, "2026-04")

        assert "adjustments" in result
        assert result["converted_count"] == 2
        # Check that independent signal has a positive adjustment (converted avg > non-converted avg)
        independent_adj = next((a for a in result["adjustments"] if a["signal"] == "independent"), None)
        assert independent_adj is not None
        assert independent_adj["converted_avg"] > independent_adj["non_converted_avg"]

    @pytest.mark.asyncio
    async def test_no_conversions_message(self):
        session = AsyncMock()

        converted_result = MagicMock()
        converted_result.all.return_value = []

        session.execute = AsyncMock(return_value=converted_result)

        result = await feedback_loop.suggest_weight_adjustments(session, "2026-04")
        assert result["message"] == "No conversions found in this period"


class TestApplyAdjustments:
    @pytest.mark.asyncio
    async def test_applies_weight_changes(self):
        session = AsyncMock()
        profile = _make_profile()
        session.get = AsyncMock(return_value=profile)
        session.flush = AsyncMock()

        with patch("src.services.feedback_loop.create_version_snapshot", new_callable=AsyncMock) as mock_snapshot:
            mock_snapshot.return_value = MagicMock()

            result = await feedback_loop.apply_adjustments(
                session,
                profile_id=profile.id,
                adjustments=[
                    {"signal": "independent", "new_weight": 18.0},
                    {"signal": "pos", "new_weight": 10.0},
                ],
                approved_by="admin@test.com",
            )

        assert result["new_version"] == 2
        assert "independent" in result["changes"]
        assert result["changes"]["independent"]["old_weight"] == 15.0
        assert result["changes"]["independent"]["new_weight"] == 18.0
        assert result["changes"]["pos"]["new_weight"] == 10.0
        mock_snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_changes_when_no_matching_signals(self):
        session = AsyncMock()
        profile = _make_profile()
        session.get = AsyncMock(return_value=profile)

        result = await feedback_loop.apply_adjustments(
            session,
            profile_id=profile.id,
            adjustments=[{"signal": "nonexistent", "new_weight": 5.0}],
        )

        assert result["message"] == "No changes applied"

    @pytest.mark.asyncio
    async def test_error_for_missing_profile(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)

        result = await feedback_loop.apply_adjustments(
            session,
            profile_id=uuid.uuid4(),
            adjustments=[{"signal": "independent", "new_weight": 18.0}],
        )

        assert result["error"] == "profile_not_found"


class TestGetFeedbackReport:
    @pytest.mark.asyncio
    async def test_returns_combined_report(self):
        session = AsyncMock()

        with patch("src.services.feedback_loop.analyze_conversions", new_callable=AsyncMock) as mock_analyze, \
             patch("src.services.feedback_loop.suggest_weight_adjustments", new_callable=AsyncMock) as mock_suggest:

            mock_analyze.return_value = {
                "period": "2026-04",
                "buckets": [
                    {"score_range": "0-25", "discovered": 10, "converted": 0, "conversion_rate": 0.0},
                    {"score_range": "75-100", "discovered": 8, "converted": 6, "conversion_rate": 75.0},
                ],
            }
            mock_suggest.return_value = {
                "profile_id": "profile-123",
                "profile_name": "Default",
                "adjustments": [
                    {"signal": "independent", "suggested_adjustment": 2.0},
                ],
            }

            result = await feedback_loop.get_feedback_report(session, "2026-04")

        assert result["period"] == "2026-04"
        assert result["total_discovered"] == 18
        assert result["total_converted"] == 6
        assert result["overall_conversion_rate"] == 33.33
        assert len(result["score_bucket_analysis"]) == 2
        assert len(result["signal_analysis"]) == 1
