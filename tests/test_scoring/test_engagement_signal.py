"""Tests for communication engagement signal (NIF-236) and scorer integration."""

from src.scoring.signals import communication_engagement_score
from src.scoring.icp_scorer import ICPScorer


class TestCommunicationEngagementScore:
    """Tests for the communication_engagement_score signal function."""

    def test_no_data_returns_none(self):
        """No events and no activities -> None (signal excluded)."""
        assert communication_engagement_score(None, None) is None
        assert communication_engagement_score([], []) is None

    def test_only_sends_no_engagement(self):
        """Only delivery events, no opens/clicks -> score near 0."""
        events = [{"event_type": "delivery"}] * 10
        score = communication_engagement_score(events, [])
        assert score == 0.0

    def test_perfect_engagement(self):
        """All emails opened and clicked, all activities replied with meetings."""
        events = [
            {"event_type": "delivery"},
            {"event_type": "open"},
            {"event_type": "click"},
        ]
        activities = [
            {"activity_type": "email_reply", "outcome": "replied"},
            {"activity_type": "meeting", "outcome": "meeting_booked"},
        ]
        score = communication_engagement_score(events, activities)
        assert score is not None
        assert score >= 0.5

    def test_opens_only(self):
        """Only opens, no clicks or replies."""
        events = [
            {"event_type": "delivery"},
            {"event_type": "delivery"},
            {"event_type": "open"},
        ]
        score = communication_engagement_score(events, [])
        assert score is not None
        # open_rate = 1/2 = 0.5, score = 0.3 * 0.5 = 0.15
        assert 0.1 <= score <= 0.2

    def test_clicks_boost_score(self):
        """Clicks add to the score beyond opens."""
        events = [
            {"event_type": "send"},
            {"event_type": "open"},
            {"event_type": "click"},
        ]
        score = communication_engagement_score(events, [])
        assert score is not None
        assert score > 0.3  # Both open + click contribute

    def test_replies_from_activities(self):
        """Reply outcome in activities contributes to score."""
        activities = [
            {"activity_type": "email_sent", "outcome": "replied"},
            {"activity_type": "call_made", "outcome": "no_answer"},
        ]
        score = communication_engagement_score([], activities)
        assert score is not None
        assert score > 0.0

    def test_meetings_from_activities(self):
        """Meeting outcomes contribute to the score."""
        activities = [
            {"activity_type": "meeting", "outcome": "meeting_booked"},
        ]
        score = communication_engagement_score([], activities)
        assert score is not None
        # meeting_rate = 1/1 = 1.0, score = 0.15 * 1.0 = 0.15
        assert 0.1 <= score <= 0.5

    def test_score_capped_at_one(self):
        """Score never exceeds 1.0."""
        events = [{"event_type": "open"}] * 100 + [{"event_type": "click"}] * 100
        activities = [
            {"activity_type": "email_reply", "outcome": "replied"},
        ] * 50 + [
            {"activity_type": "meeting", "outcome": "meeting_booked"},
        ] * 50
        score = communication_engagement_score(events, activities)
        assert score is not None
        assert score <= 1.0

    def test_empty_event_types_handled(self):
        """Events with None/empty event_type don't crash."""
        events = [{"event_type": None}, {"event_type": ""}]
        score = communication_engagement_score(events, [])
        assert score is not None
        assert score == 0.0


class TestICPScorerWithCommunicationEngagement:
    """Tests for the ICPScorer integration with the 9th signal."""

    def setup_method(self):
        self.scorer = ICPScorer()

    def test_no_comm_data_same_as_before(self):
        """Without communication data, score should be same as 8-signal model."""
        restaurant = {"name": "Joe's Pizza", "review_count": 250, "rating": 4.5}
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]

        # Score without comm data
        result = self.scorer.score_restaurant(restaurant, records, density_score=0.5)
        assert result["communication_engagement"] is None
        assert result["total_icp_score"] > 0

    def test_comm_data_included_in_result(self):
        """When communication data is provided, score is included in result."""
        restaurant = {"name": "Joe's Pizza", "review_count": 250, "rating": 4.5}
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]
        tracker_events = [
            {"event_type": "delivery"},
            {"event_type": "open"},
            {"event_type": "click"},
        ]
        result = self.scorer.score_restaurant(
            restaurant, records, density_score=0.5,
            tracker_events=tracker_events,
        )
        assert result["communication_engagement"] is not None
        assert result["communication_engagement"] > 0

    def test_result_includes_all_fields(self):
        """Verify communication_engagement field is always present in result."""
        result = self.scorer.score_restaurant(
            {"name": "T", "review_count": 0, "rating": 0}, [], 0,
        )
        assert "communication_engagement" in result

    def test_weight_redistribution_preserves_score_range(self):
        """With default weight=0, scores should stay the same whether comm data is present or not."""
        restaurant = {"name": "Joe's Pizza", "review_count": 250, "rating": 4.5}
        records = [{"source": "doordash", "has_delivery": True, "delivery_platform": "doordash"}]

        result_no_comm = self.scorer.score_restaurant(restaurant, records, density_score=0.5)
        result_with_comm = self.scorer.score_restaurant(
            restaurant, records, density_score=0.5,
            tracker_events=[{"event_type": "delivery"}, {"event_type": "open"}],
        )

        # With default weight=0, both should have the same total score
        assert result_no_comm["total_icp_score"] == result_with_comm["total_icp_score"]
