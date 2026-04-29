"""Tests for intent scoring signal (NIF-261)."""

from datetime import datetime, timezone, timedelta

from src.scoring.signals import intent_score


class TestIntentScore:
    """Tests for the intent_score behavioural pattern classifier."""

    def test_no_data_returns_unknown(self):
        """No events and no activities returns (0.0, 'unknown')."""
        score, label = intent_score(None, None)
        assert score == 0.0
        assert label == "unknown"

        score, label = intent_score([], [])
        assert score == 0.0
        assert label == "unknown"

    def test_reply_is_high_intent(self):
        """A reply activity produces high_intent."""
        activities = [{"activity_type": "email_reply", "outcome": "replied"}]
        score, label = intent_score([], activities)
        assert score == 1.0
        assert label == "high_intent"

    def test_meeting_is_high_intent(self):
        """A meeting booked produces high_intent."""
        activities = [{"activity_type": "meeting", "outcome": "meeting_booked"}]
        score, label = intent_score([], activities)
        assert score == 1.0
        assert label == "high_intent"

    def test_demo_scheduled_is_high_intent(self):
        """demo_scheduled outcome counts as high intent."""
        activities = [{"activity_type": "call", "outcome": "demo_scheduled"}]
        score, label = intent_score([], activities)
        assert score == 1.0
        assert label == "high_intent"

    def test_pricing_click_is_evaluating(self):
        """Clicking a pricing link produces evaluating."""
        events = [
            {"event_type": "click", "event_metadata": {"url": "https://example.com/pricing"}},
        ]
        score, label = intent_score(events, [])
        assert score == 0.7
        assert label == "evaluating"

    def test_demo_link_click_is_evaluating(self):
        """Clicking a demo link produces evaluating."""
        events = [
            {"event_type": "click", "event_metadata": {"url": "https://example.com/demo"}},
        ]
        score, label = intent_score(events, [])
        assert score == 0.7
        assert label == "evaluating"

    def test_three_opens_is_researching(self):
        """3+ email opens without clicks produces researching."""
        events = [
            {"event_type": "open"},
            {"event_type": "open"},
            {"event_type": "open"},
        ]
        score, label = intent_score(events, [])
        assert score == 0.4
        assert label == "researching"

    def test_two_opens_not_researching(self):
        """2 opens is below the 3-open threshold."""
        events = [
            {"event_type": "open"},
            {"event_type": "open"},
        ]
        score, label = intent_score(events, [])
        assert label != "researching"
        assert score < 0.4

    def test_single_open_is_aware(self):
        """A single recent open produces aware."""
        now = datetime.now(timezone.utc)
        events = [
            {"event_type": "open", "occurred_at": now.isoformat()},
        ]
        score, label = intent_score(events, [])
        assert score == 0.3
        assert label == "aware"

    def test_cold_after_30_days(self):
        """No engagement for 30+ days after contact is cold."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        events = [
            {"event_type": "delivery", "occurred_at": old_date},
        ]
        score, label = intent_score(events, [])
        assert score == 0.1
        assert label == "cold"

    def test_high_intent_beats_evaluating(self):
        """Reply + pricing click: high_intent wins."""
        events = [
            {"event_type": "click", "event_metadata": {"url": "https://example.com/pricing"}},
        ]
        activities = [
            {"activity_type": "email_reply", "outcome": "replied"},
        ]
        score, label = intent_score(events, activities)
        assert score == 1.0
        assert label == "high_intent"

    def test_evaluating_beats_researching(self):
        """Pricing click + 3 opens: evaluating wins."""
        events = [
            {"event_type": "open"},
            {"event_type": "open"},
            {"event_type": "open"},
            {"event_type": "click", "event_metadata": {"url": "https://example.com/pricing"}},
        ]
        score, label = intent_score(events, [])
        assert score == 0.7
        assert label == "evaluating"

    def test_non_pricing_click_not_evaluating(self):
        """Regular click (not pricing/demo) does not trigger evaluating."""
        events = [
            {"event_type": "click", "event_metadata": {"url": "https://example.com/blog/article"}},
        ]
        score, label = intent_score(events, [])
        assert label != "evaluating"

    def test_none_event_metadata_handled(self):
        """Click event with None metadata doesn't crash."""
        events = [
            {"event_type": "click", "event_metadata": None},
        ]
        score, label = intent_score(events, [])
        assert isinstance(score, float)

    def test_empty_event_types_handled(self):
        """Events with None/empty event_type don't crash."""
        events = [{"event_type": None}, {"event_type": ""}]
        score, label = intent_score(events, [])
        assert score == 0.1  # cold (has events but no engagement, recent)
        assert label == "cold"

    def test_datetime_objects_in_occurred_at(self):
        """Supports datetime objects (not just strings) for occurred_at."""
        old_date = datetime.now(timezone.utc) - timedelta(days=45)
        events = [
            {"event_type": "delivery", "occurred_at": old_date},
        ]
        score, label = intent_score(events, [])
        assert score == 0.1
        assert label == "cold"

    def test_score_always_between_0_and_1(self):
        """Score is always in [0, 1]."""
        # Various combinations
        test_cases = [
            ([], []),
            ([{"event_type": "open"}] * 10, []),
            ([], [{"activity_type": "meeting", "outcome": "meeting_booked"}]),
            ([{"event_type": "click", "event_metadata": {"url": "/pricing"}}], []),
        ]
        for events, activities in test_cases:
            score, label = intent_score(events, activities)
            assert 0.0 <= score <= 1.0, f"Score {score} out of range for label {label}"
