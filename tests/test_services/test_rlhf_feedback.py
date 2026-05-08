"""Tests for RLHF feedback collection service (NIF-274).

Covers:
- record_feedback creates a feedback entry
- record_feedback validates rating range
- record_feedback with optional run_id
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.rlhf_feedback import record_feedback, VALID_RATINGS


class TestRecordFeedback:
    @pytest.mark.asyncio
    async def test_record_valid_feedback(self):
        """Records feedback with valid rating."""
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        feedback = await record_feedback(
            session=session,
            agent_name="qualification",
            input_context={"restaurant_id": "abc"},
            output_result={"status": "qualified"},
            rating=4,
            feedback_text="Good result",
            rated_by="rep@example.com",
        )

        session.add.assert_called_once()
        assert feedback.agent_name == "qualification"
        assert feedback.rating == 4
        assert feedback.rated_by == "rep@example.com"

    @pytest.mark.asyncio
    async def test_invalid_rating_raises(self):
        """Rejects ratings outside 1-5."""
        session = AsyncMock()
        with pytest.raises(ValueError, match="Rating must be 1-5"):
            await record_feedback(
                session=session,
                agent_name="test",
                input_context={},
                output_result={},
                rating=0,
                rated_by="tester",
            )

    @pytest.mark.asyncio
    async def test_invalid_rating_too_high(self):
        """Rejects ratings above 5."""
        session = AsyncMock()
        with pytest.raises(ValueError, match="Rating must be 1-5"):
            await record_feedback(
                session=session,
                agent_name="test",
                input_context={},
                output_result={},
                rating=6,
                rated_by="tester",
            )

    @pytest.mark.asyncio
    async def test_record_with_run_id(self):
        """Records feedback linked to a specific agent run."""
        session = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        run_id = uuid.uuid4()

        feedback = await record_feedback(
            session=session,
            agent_name="outreach",
            input_context={"lead_id": "xyz"},
            output_result={"channel": "sms"},
            rating=5,
            rated_by="rep2@example.com",
            run_id=run_id,
        )

        assert feedback.run_id == run_id
        assert feedback.rating == 5

    @pytest.mark.asyncio
    async def test_all_valid_ratings(self):
        """All ratings 1-5 are accepted."""
        for r in VALID_RATINGS:
            session = AsyncMock()
            session.add = MagicMock()
            session.flush = AsyncMock()
            feedback = await record_feedback(
                session=session,
                agent_name="test",
                input_context={},
                output_result={},
                rating=r,
                rated_by="tester",
            )
            assert feedback.rating == r
