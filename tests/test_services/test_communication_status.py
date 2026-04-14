"""Tests for NIF-222: Unified communication status state machine.

Covers:
- CommunicationStatus enum values
- is_valid_transition: all valid and invalid paths
- get_terminal_states
- transition_status: success, invalid, target-not-found
- mark_as_* helper functions
- get_communication_summary aggregation
- compute_engagement_level: high/medium/low/none
- TrackerEvent creation on status change
- Concurrent update safety (mock-based)
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.services.communication_status import (
    CommunicationStatus,
    compute_engagement_level,
    get_communication_summary,
    get_terminal_states,
    is_valid_transition,
    mark_as_bounced,
    mark_as_clicked,
    mark_as_delivered,
    mark_as_failed,
    mark_as_opened,
    mark_as_opted_out,
    mark_as_replied,
    mark_as_sent,
    transition_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(
    target_id=None,
    lead_id=None,
    communication_status="queued",
):
    """Build a minimal OutreachTarget-like mock."""
    t = MagicMock()
    t.id = target_id or uuid.uuid4()
    t.lead_id = lead_id or uuid.uuid4()
    t.communication_status = communication_status
    return t


def _make_session(target=None, tracker_events=None):
    """Build an AsyncSession mock that returns a target on get() and tracker events on execute()."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=target)
    session.add = MagicMock()
    session.flush = AsyncMock()

    # Default execute returns empty
    if tracker_events is not None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = tracker_events
        mock_result.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)
    else:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_result.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

    return session


# ---------------------------------------------------------------------------
# 1. CommunicationStatus enum
# ---------------------------------------------------------------------------

class TestCommunicationStatusEnum:
    def test_all_expected_values_exist(self):
        expected = {"queued", "sent", "delivered", "opened", "clicked", "replied",
                    "bounced", "failed", "opted_out"}
        actual = {s.value for s in CommunicationStatus}
        assert actual == expected

    def test_queued_value(self):
        assert CommunicationStatus.QUEUED.value == "queued"

    def test_sent_value(self):
        assert CommunicationStatus.SENT.value == "sent"

    def test_delivered_value(self):
        assert CommunicationStatus.DELIVERED.value == "delivered"

    def test_opened_value(self):
        assert CommunicationStatus.OPENED.value == "opened"

    def test_clicked_value(self):
        assert CommunicationStatus.CLICKED.value == "clicked"

    def test_replied_value(self):
        assert CommunicationStatus.REPLIED.value == "replied"

    def test_bounced_value(self):
        assert CommunicationStatus.BOUNCED.value == "bounced"

    def test_failed_value(self):
        assert CommunicationStatus.FAILED.value == "failed"

    def test_opted_out_value(self):
        assert CommunicationStatus.OPTED_OUT.value == "opted_out"

    def test_is_string_enum(self):
        assert isinstance(CommunicationStatus.QUEUED, str)
        assert CommunicationStatus.SENT == "sent"

    def test_construction_from_string(self):
        assert CommunicationStatus("queued") is CommunicationStatus.QUEUED
        assert CommunicationStatus("clicked") is CommunicationStatus.CLICKED


# ---------------------------------------------------------------------------
# 2. is_valid_transition — valid paths
# ---------------------------------------------------------------------------

class TestIsValidTransitionValid:
    def test_queued_to_sent(self):
        assert is_valid_transition("queued", "sent") is True

    def test_queued_to_failed(self):
        assert is_valid_transition("queued", "failed") is True

    def test_queued_to_opted_out(self):
        assert is_valid_transition("queued", "opted_out") is True

    def test_sent_to_delivered(self):
        assert is_valid_transition("sent", "delivered") is True

    def test_sent_to_bounced(self):
        assert is_valid_transition("sent", "bounced") is True

    def test_sent_to_failed(self):
        assert is_valid_transition("sent", "failed") is True

    def test_delivered_to_opened(self):
        assert is_valid_transition("delivered", "opened") is True

    def test_delivered_to_bounced(self):
        assert is_valid_transition("delivered", "bounced") is True

    def test_opened_to_clicked(self):
        assert is_valid_transition("opened", "clicked") is True

    def test_opened_to_replied(self):
        assert is_valid_transition("opened", "replied") is True

    def test_clicked_to_replied(self):
        assert is_valid_transition("clicked", "replied") is True

    def test_accepts_enum_objects(self):
        assert is_valid_transition(CommunicationStatus.QUEUED, CommunicationStatus.SENT) is True

    def test_accepts_mixed_enum_and_string(self):
        assert is_valid_transition(CommunicationStatus.SENT, "delivered") is True


# ---------------------------------------------------------------------------
# 3. is_valid_transition — invalid paths
# ---------------------------------------------------------------------------

class TestIsValidTransitionInvalid:
    def test_bounced_to_sent(self):
        assert is_valid_transition("bounced", "sent") is False

    def test_bounced_to_delivered(self):
        assert is_valid_transition("bounced", "delivered") is False

    def test_failed_to_sent(self):
        assert is_valid_transition("failed", "sent") is False

    def test_replied_to_clicked(self):
        assert is_valid_transition("replied", "clicked") is False

    def test_opted_out_to_sent(self):
        assert is_valid_transition("opted_out", "sent") is False

    def test_queued_to_delivered(self):
        assert is_valid_transition("queued", "delivered") is False

    def test_queued_to_opened(self):
        assert is_valid_transition("queued", "opened") is False

    def test_sent_to_opened(self):
        assert is_valid_transition("sent", "opened") is False

    def test_sent_to_replied(self):
        assert is_valid_transition("sent", "replied") is False

    def test_delivered_to_replied(self):
        assert is_valid_transition("delivered", "replied") is False

    def test_clicked_to_delivered(self):
        assert is_valid_transition("clicked", "delivered") is False

    def test_opened_to_bounced(self):
        assert is_valid_transition("opened", "bounced") is False

    def test_unknown_current_status(self):
        assert is_valid_transition("unknown_status", "sent") is False

    def test_unknown_new_status(self):
        assert is_valid_transition("queued", "unknown_status") is False

    def test_same_status(self):
        assert is_valid_transition("sent", "sent") is False


# ---------------------------------------------------------------------------
# 4. Terminal states
# ---------------------------------------------------------------------------

class TestGetTerminalStates:
    def test_returns_set(self):
        assert isinstance(get_terminal_states(), set)

    def test_contains_replied(self):
        assert CommunicationStatus.REPLIED in get_terminal_states()

    def test_contains_bounced(self):
        assert CommunicationStatus.BOUNCED in get_terminal_states()

    def test_contains_failed(self):
        assert CommunicationStatus.FAILED in get_terminal_states()

    def test_contains_opted_out(self):
        assert CommunicationStatus.OPTED_OUT in get_terminal_states()

    def test_does_not_contain_queued(self):
        assert CommunicationStatus.QUEUED not in get_terminal_states()

    def test_does_not_contain_sent(self):
        assert CommunicationStatus.SENT not in get_terminal_states()

    def test_does_not_contain_delivered(self):
        assert CommunicationStatus.DELIVERED not in get_terminal_states()

    def test_does_not_contain_opened(self):
        assert CommunicationStatus.OPENED not in get_terminal_states()

    def test_does_not_contain_clicked(self):
        assert CommunicationStatus.CLICKED not in get_terminal_states()

    def test_terminal_states_cannot_transition(self):
        for terminal in get_terminal_states():
            for status in CommunicationStatus:
                assert is_valid_transition(terminal, status) is False, (
                    f"Terminal state {terminal} should not allow transition to {status}"
                )

    def test_returns_copy(self):
        ts1 = get_terminal_states()
        ts1.add(CommunicationStatus.QUEUED)
        ts2 = get_terminal_states()
        assert CommunicationStatus.QUEUED not in ts2


# ---------------------------------------------------------------------------
# 5. transition_status
# ---------------------------------------------------------------------------

class TestTransitionStatus:
    @pytest.mark.asyncio
    async def test_valid_transition_returns_true(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await transition_status(session, target.id, "sent", "email")
        assert result is True

    @pytest.mark.asyncio
    async def test_valid_transition_updates_model(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "email")
        assert target.communication_status == "sent"

    @pytest.mark.asyncio
    async def test_valid_transition_creates_tracker_event(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "email")
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        from src.db.models import TrackerEvent
        assert isinstance(added, TrackerEvent)
        assert added.channel == "email"
        assert added.target_id == target.id
        assert added.lead_id == target.lead_id

    @pytest.mark.asyncio
    async def test_valid_transition_flushes_session(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "email")
        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_transition_returns_false(self):
        target = _make_target(communication_status="bounced")
        session = _make_session(target=target)
        result = await transition_status(session, target.id, "sent", "email")
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_transition_does_not_update_model(self):
        target = _make_target(communication_status="bounced")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "email")
        assert target.communication_status == "bounced"

    @pytest.mark.asyncio
    async def test_invalid_transition_does_not_create_event(self):
        target = _make_target(communication_status="bounced")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "email")
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_target_not_found_returns_false(self):
        session = _make_session(target=None)
        result = await transition_status(session, uuid.uuid4(), "sent", "email")
        assert result is False

    @pytest.mark.asyncio
    async def test_metadata_stored_in_event(self):
        target = _make_target(communication_status="sent")
        session = _make_session(target=target)
        await transition_status(session, target.id, "delivered", "sms", metadata={"custom": "value"})
        added = session.add.call_args[0][0]
        assert added.event_metadata == {"custom": "value"}

    @pytest.mark.asyncio
    async def test_none_metadata_defaults_to_empty_dict(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "email", metadata=None)
        added = session.add.call_args[0][0]
        assert added.event_metadata == {}

    @pytest.mark.asyncio
    async def test_accepts_enum_for_new_status(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await transition_status(
            session, target.id, CommunicationStatus.SENT, "email"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_provider_event_id_is_unique(self):
        target1 = _make_target(communication_status="queued")
        target2 = _make_target(communication_status="queued")
        session1 = _make_session(target=target1)
        session2 = _make_session(target=target2)

        await transition_status(session1, target1.id, "sent", "email")
        await transition_status(session2, target2.id, "sent", "email")

        ev1 = session1.add.call_args[0][0]
        ev2 = session2.add.call_args[0][0]
        assert ev1.provider_event_id != ev2.provider_event_id


# ---------------------------------------------------------------------------
# 6. mark_as_* helper functions
# ---------------------------------------------------------------------------

class TestMarkAsHelpers:
    @pytest.mark.asyncio
    async def test_mark_as_sent_valid(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await mark_as_sent(session, target.id, "email", external_message_id="msg-123")
        assert result is True
        assert target.communication_status == "sent"

    @pytest.mark.asyncio
    async def test_mark_as_sent_stores_external_id(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await mark_as_sent(session, target.id, "email", external_message_id="msg-abc")
        ev = session.add.call_args[0][0]
        assert ev.event_metadata == {"external_message_id": "msg-abc"}

    @pytest.mark.asyncio
    async def test_mark_as_sent_without_external_id(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await mark_as_sent(session, target.id, "sms")
        assert result is True

    @pytest.mark.asyncio
    async def test_mark_as_delivered_valid(self):
        target = _make_target(communication_status="sent")
        session = _make_session(target=target)
        result = await mark_as_delivered(session, target.id, "email")
        assert result is True
        assert target.communication_status == "delivered"

    @pytest.mark.asyncio
    async def test_mark_as_delivered_invalid_from_queued(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await mark_as_delivered(session, target.id, "email")
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_as_opened_valid(self):
        target = _make_target(communication_status="delivered")
        session = _make_session(target=target)
        result = await mark_as_opened(session, target.id, "email")
        assert result is True
        assert target.communication_status == "opened"

    @pytest.mark.asyncio
    async def test_mark_as_clicked_valid(self):
        target = _make_target(communication_status="opened")
        session = _make_session(target=target)
        result = await mark_as_clicked(session, target.id, "email")
        assert result is True
        assert target.communication_status == "clicked"

    @pytest.mark.asyncio
    async def test_mark_as_bounced_valid_from_sent(self):
        target = _make_target(communication_status="sent")
        session = _make_session(target=target)
        result = await mark_as_bounced(session, target.id, "email", bounce_type="hard")
        assert result is True
        assert target.communication_status == "bounced"

    @pytest.mark.asyncio
    async def test_mark_as_bounced_stores_bounce_type(self):
        target = _make_target(communication_status="sent")
        session = _make_session(target=target)
        await mark_as_bounced(session, target.id, "email", bounce_type="soft")
        ev = session.add.call_args[0][0]
        assert ev.event_metadata["bounce_type"] == "soft"

    @pytest.mark.asyncio
    async def test_mark_as_bounced_default_type_is_hard(self):
        target = _make_target(communication_status="sent")
        session = _make_session(target=target)
        await mark_as_bounced(session, target.id, "email")
        ev = session.add.call_args[0][0]
        assert ev.event_metadata["bounce_type"] == "hard"

    @pytest.mark.asyncio
    async def test_mark_as_failed_valid(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await mark_as_failed(session, target.id, "sms", error_reason="timeout")
        assert result is True
        assert target.communication_status == "failed"

    @pytest.mark.asyncio
    async def test_mark_as_failed_stores_error_reason(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await mark_as_failed(session, target.id, "sms", error_reason="rate_limit")
        ev = session.add.call_args[0][0]
        assert ev.event_metadata["error_reason"] == "rate_limit"

    @pytest.mark.asyncio
    async def test_mark_as_failed_without_reason(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await mark_as_failed(session, target.id, "email")
        assert result is True

    @pytest.mark.asyncio
    async def test_mark_as_opted_out_valid(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await mark_as_opted_out(session, target.id, "email")
        assert result is True
        assert target.communication_status == "opted_out"

    @pytest.mark.asyncio
    async def test_mark_as_opted_out_invalid_from_sent(self):
        target = _make_target(communication_status="sent")
        session = _make_session(target=target)
        result = await mark_as_opted_out(session, target.id, "email")
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_as_replied_valid_from_opened(self):
        target = _make_target(communication_status="opened")
        session = _make_session(target=target)
        result = await mark_as_replied(session, target.id, "email")
        assert result is True
        assert target.communication_status == "replied"

    @pytest.mark.asyncio
    async def test_mark_as_replied_valid_from_clicked(self):
        target = _make_target(communication_status="clicked")
        session = _make_session(target=target)
        result = await mark_as_replied(session, target.id, "email")
        assert result is True
        assert target.communication_status == "replied"

    @pytest.mark.asyncio
    async def test_mark_as_replied_invalid_from_queued(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        result = await mark_as_replied(session, target.id, "email")
        assert result is False
        assert target.communication_status == "queued"

    @pytest.mark.asyncio
    async def test_mark_as_replied_invalid_from_sent(self):
        target = _make_target(communication_status="sent")
        session = _make_session(target=target)
        result = await mark_as_replied(session, target.id, "email")
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_as_replied_invalid_from_bounced(self):
        target = _make_target(communication_status="bounced")
        session = _make_session(target=target)
        result = await mark_as_replied(session, target.id, "email")
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_as_replied_creates_read_event(self):
        target = _make_target(communication_status="opened")
        session = _make_session(target=target)
        await mark_as_replied(session, target.id, "email")
        ev = session.add.call_args[0][0]
        from src.db.models import TrackerEvent
        assert isinstance(ev, TrackerEvent)
        assert ev.event_type == "read"

    @pytest.mark.asyncio
    async def test_all_helpers_return_false_for_missing_target(self):
        session = _make_session(target=None)
        tid = uuid.uuid4()
        assert await mark_as_sent(session, tid, "email") is False
        assert await mark_as_delivered(session, tid, "email") is False
        assert await mark_as_opened(session, tid, "email") is False
        assert await mark_as_clicked(session, tid, "email") is False
        assert await mark_as_bounced(session, tid, "email") is False
        assert await mark_as_failed(session, tid, "email") is False
        assert await mark_as_opted_out(session, tid, "email") is False
        assert await mark_as_replied(session, tid, "email") is False


# ---------------------------------------------------------------------------
# 7. get_communication_summary
# ---------------------------------------------------------------------------

class TestGetCommunicationSummary:
    def _make_target_with_status(self, status):
        t = MagicMock()
        t.communication_status = status
        return t

    @pytest.mark.asyncio
    async def test_empty_lead_returns_zero_total(self):
        session = AsyncMock()
        # targets query
        targets_result = MagicMock()
        targets_result.scalars.return_value.all.return_value = []
        # channel query
        channel_result = MagicMock()
        channel_result.all.return_value = []
        session.execute = AsyncMock(side_effect=[targets_result, channel_result])

        summary = await get_communication_summary(session, uuid.uuid4())
        assert summary["total"] == 0
        assert summary["by_status"] == {}
        assert summary["by_channel"] == {}

    @pytest.mark.asyncio
    async def test_counts_by_status(self):
        targets = [
            self._make_target_with_status("sent"),
            self._make_target_with_status("sent"),
            self._make_target_with_status("delivered"),
        ]
        session = AsyncMock()
        targets_result = MagicMock()
        targets_result.scalars.return_value.all.return_value = targets
        channel_result = MagicMock()
        channel_result.all.return_value = []
        session.execute = AsyncMock(side_effect=[targets_result, channel_result])

        summary = await get_communication_summary(session, uuid.uuid4())
        assert summary["total"] == 3
        assert summary["by_status"]["sent"] == 2
        assert summary["by_status"]["delivered"] == 1

    @pytest.mark.asyncio
    async def test_null_status_counts_as_queued(self):
        target = self._make_target_with_status(None)
        session = AsyncMock()
        targets_result = MagicMock()
        targets_result.scalars.return_value.all.return_value = [target]
        channel_result = MagicMock()
        channel_result.all.return_value = []
        session.execute = AsyncMock(side_effect=[targets_result, channel_result])

        summary = await get_communication_summary(session, uuid.uuid4())
        assert summary["by_status"]["queued"] == 1

    @pytest.mark.asyncio
    async def test_by_channel_aggregation(self):
        session = AsyncMock()
        targets_result = MagicMock()
        targets_result.scalars.return_value.all.return_value = []
        channel_result = MagicMock()
        channel_result.all.return_value = [("email", 5), ("sms", 2)]
        session.execute = AsyncMock(side_effect=[targets_result, channel_result])

        summary = await get_communication_summary(session, uuid.uuid4())
        assert summary["by_channel"]["email"] == 5
        assert summary["by_channel"]["sms"] == 2

    @pytest.mark.asyncio
    async def test_summary_keys_always_present(self):
        session = AsyncMock()
        targets_result = MagicMock()
        targets_result.scalars.return_value.all.return_value = []
        channel_result = MagicMock()
        channel_result.all.return_value = []
        session.execute = AsyncMock(side_effect=[targets_result, channel_result])

        summary = await get_communication_summary(session, uuid.uuid4())
        assert "total" in summary
        assert "by_status" in summary
        assert "by_channel" in summary


# ---------------------------------------------------------------------------
# 8. compute_engagement_level
# ---------------------------------------------------------------------------

class TestComputeEngagementLevel:
    def _make_execute_with_event_types(self, session, event_types):
        result = MagicMock()
        result.all.return_value = [(et,) for et in event_types]
        session.execute = AsyncMock(return_value=result)

    @pytest.mark.asyncio
    async def test_no_events_returns_none(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, [])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "none"

    @pytest.mark.asyncio
    async def test_click_returns_high(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, ["delivery", "open", "click"])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "high"

    @pytest.mark.asyncio
    async def test_read_returns_high(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, ["delivery", "open", "read"])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "high"

    @pytest.mark.asyncio
    async def test_click_only_returns_high(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, ["click"])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "high"

    @pytest.mark.asyncio
    async def test_open_without_click_returns_medium(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, ["delivery", "open"])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "medium"

    @pytest.mark.asyncio
    async def test_open_only_returns_medium(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, ["open"])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "medium"

    @pytest.mark.asyncio
    async def test_delivery_only_returns_low(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, ["delivery"])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "low"

    @pytest.mark.asyncio
    async def test_bounce_only_returns_low(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, ["bounce"])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "low"

    @pytest.mark.asyncio
    async def test_multiple_deliveries_returns_low(self):
        session = AsyncMock()
        self._make_execute_with_event_types(session, ["delivery", "delivery", "delivery"])
        level = await compute_engagement_level(session, uuid.uuid4())
        assert level == "low"


# ---------------------------------------------------------------------------
# 9. OutreachTarget model has communication_status column
# ---------------------------------------------------------------------------

class TestOutreachTargetCommunicationStatusColumn:
    def test_column_exists(self):
        from src.db.models import OutreachTarget
        from sqlalchemy import inspect
        mapper = inspect(OutreachTarget)
        col_names = [c.key for c in mapper.columns]
        assert "communication_status" in col_names

    def test_default_value_is_queued(self):
        """Column declaration default is 'queued'; SQLAlchemy defaults fire on INSERT
        not Python construction, so we verify via the column definition."""
        from src.db.models import OutreachTarget
        col = OutreachTarget.__table__.c["communication_status"]
        assert col.default.arg == "queued"

    def test_can_set_status(self):
        from src.db.models import OutreachTarget
        target = OutreachTarget(
            campaign_id=uuid.uuid4(),
            restaurant_id=uuid.uuid4(),
            communication_status="sent",
        )
        assert target.communication_status == "sent"


# ---------------------------------------------------------------------------
# 10. TrackerEvent integration on status change
# ---------------------------------------------------------------------------

class TestTrackerEventCreatedOnTransition:
    @pytest.mark.asyncio
    async def test_sent_creates_delivery_event(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "email")
        ev = session.add.call_args[0][0]
        assert ev.event_type == "delivery"

    @pytest.mark.asyncio
    async def test_opened_creates_open_event(self):
        target = _make_target(communication_status="delivered")
        session = _make_session(target=target)
        await transition_status(session, target.id, "opened", "email")
        ev = session.add.call_args[0][0]
        assert ev.event_type == "open"

    @pytest.mark.asyncio
    async def test_clicked_creates_click_event(self):
        target = _make_target(communication_status="opened")
        session = _make_session(target=target)
        await transition_status(session, target.id, "clicked", "email")
        ev = session.add.call_args[0][0]
        assert ev.event_type == "click"

    @pytest.mark.asyncio
    async def test_bounced_creates_bounce_event(self):
        target = _make_target(communication_status="sent")
        session = _make_session(target=target)
        await transition_status(session, target.id, "bounced", "email")
        ev = session.add.call_args[0][0]
        assert ev.event_type == "bounce"

    @pytest.mark.asyncio
    async def test_opted_out_creates_unsubscribe_event(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await transition_status(session, target.id, "opted_out", "email")
        ev = session.add.call_args[0][0]
        assert ev.event_type == "unsubscribe"

    @pytest.mark.asyncio
    async def test_replied_creates_read_event(self):
        target = _make_target(communication_status="opened")
        session = _make_session(target=target)
        await transition_status(session, target.id, "replied", "email")
        ev = session.add.call_args[0][0]
        assert ev.event_type == "read"

    @pytest.mark.asyncio
    async def test_event_has_occurred_at(self):
        before = datetime.now(timezone.utc)
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "email")
        ev = session.add.call_args[0][0]
        assert ev.occurred_at >= before

    @pytest.mark.asyncio
    async def test_event_channel_matches_arg(self):
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)
        await transition_status(session, target.id, "sent", "whatsapp")
        ev = session.add.call_args[0][0]
        assert ev.channel == "whatsapp"


# ---------------------------------------------------------------------------
# 11. Concurrent update safety (mock-based)
# ---------------------------------------------------------------------------

class TestConcurrentUpdateSafety:
    @pytest.mark.asyncio
    async def test_second_transition_sees_updated_status(self):
        """Simulate two sequential calls: first succeeds, second gets rejected
        because the target's status has already advanced."""
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)

        # First call: queued → sent
        result1 = await transition_status(session, target.id, "sent", "email")
        assert result1 is True
        assert target.communication_status == "sent"

        # Reset session.add/flush call counts
        session.add.reset_mock()
        session.flush.reset_mock()

        # Second call: attempt queued → sent again — now invalid because target is "sent"
        result2 = await transition_status(session, target.id, "sent", "email")
        assert result2 is False
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_helpers_chain_correctly(self):
        """Full happy-path chain: queued → sent → delivered → opened → clicked → replied."""
        target = _make_target(communication_status="queued")
        session = _make_session(target=target)

        assert await mark_as_sent(session, target.id, "email") is True
        assert await mark_as_delivered(session, target.id, "email") is True
        assert await mark_as_opened(session, target.id, "email") is True
        assert await mark_as_clicked(session, target.id, "email") is True
        # After clicked → replied is the next valid transition
        result = await transition_status(session, target.id, "replied", "email")
        assert result is True
        assert target.communication_status == "replied"

    @pytest.mark.asyncio
    async def test_terminal_state_rejects_all_further_transitions(self):
        for terminal in ("replied", "bounced", "failed", "opted_out"):
            target = _make_target(communication_status=terminal)
            session = _make_session(target=target)
            for new_status in CommunicationStatus:
                result = await transition_status(session, target.id, new_status.value, "email")
                assert result is False, (
                    f"Terminal state {terminal} should reject transition to {new_status.value}"
                )
