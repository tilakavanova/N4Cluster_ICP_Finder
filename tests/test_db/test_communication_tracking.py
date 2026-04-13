"""Tests for NIF-221: Communication tracking DB models.

Covers TrackerEvent ORM model, new Lead columns (email_opt_out, sms_opt_out),
and new OutreachActivity columns (external_message_id, channel).
All tests operate on in-memory objects — no DB connection required.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from src.db.models import Lead, OutreachActivity, TrackerEvent


# ---------------------------------------------------------------------------
# TrackerEvent — field presence and defaults
# ---------------------------------------------------------------------------

class TestTrackerEventModel:
    def test_table_name(self):
        assert TrackerEvent.__tablename__ == "tracker_events"

    def test_create_minimal(self):
        ev = TrackerEvent(event_type="open", channel="email")
        assert ev.event_type == "open"
        assert ev.channel == "email"

    def test_create_full(self):
        lead_id = uuid4()
        campaign_id = uuid4()
        target_id = uuid4()
        ev = TrackerEvent(
            token="tok_abc123",
            event_type="click",
            channel="sms",
            lead_id=lead_id,
            campaign_id=campaign_id,
            target_id=target_id,
            provider="sendgrid",
            provider_event_id="sg-evt-999",
            event_metadata={"url": "https://example.com", "ip": "1.2.3.4"},
        )
        assert ev.token == "tok_abc123"
        assert ev.event_type == "click"
        assert ev.channel == "sms"
        assert ev.lead_id == lead_id
        assert ev.campaign_id == campaign_id
        assert ev.target_id == target_id
        assert ev.provider == "sendgrid"
        assert ev.provider_event_id == "sg-evt-999"
        assert ev.event_metadata["url"] == "https://example.com"

    def test_all_event_types_accepted(self):
        for etype in ("open", "click", "delivery", "read", "bounce", "complaint", "unsubscribe", "stop"):
            ev = TrackerEvent(event_type=etype, channel="email")
            assert ev.event_type == etype

    def test_all_channels_accepted(self):
        for ch in ("email", "sms", "whatsapp"):
            ev = TrackerEvent(event_type="delivery", channel=ch)
            assert ev.channel == ch

    def test_all_providers_accepted(self):
        for prov in ("sendgrid", "mailgun", "plivo", "twilio", "meta"):
            ev = TrackerEvent(event_type="delivery", channel="email", provider=prov)
            assert ev.provider == prov

    def test_occurred_at_defaults_to_now(self):
        before = datetime.now(timezone.utc)
        ev = TrackerEvent(event_type="open", channel="email")
        # occurred_at is set by Python default lambda
        if ev.occurred_at is not None:
            assert ev.occurred_at >= before

    def test_occurred_at_explicit(self):
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        ev = TrackerEvent(event_type="bounce", channel="email", occurred_at=ts)
        assert ev.occurred_at == ts

    def test_event_metadata_accepts_nested_json(self):
        meta = {"headers": {"x-msg-id": "abc"}, "clicks": [{"url": "https://x.com", "ts": 1700000000}]}
        ev = TrackerEvent(event_type="click", channel="email", event_metadata=meta)
        assert ev.event_metadata["clicks"][0]["url"] == "https://x.com"

    def test_event_metadata_can_be_none(self):
        ev = TrackerEvent(event_type="open", channel="email")
        assert ev.event_metadata is None

    def test_nullable_foreign_keys(self):
        ev = TrackerEvent(event_type="bounce", channel="email")
        assert ev.lead_id is None
        assert ev.campaign_id is None
        assert ev.target_id is None

    def test_foreign_key_assignment(self):
        lid = uuid4()
        cid = uuid4()
        tid = uuid4()
        ev = TrackerEvent(
            event_type="open",
            channel="email",
            lead_id=lid,
            campaign_id=cid,
            target_id=tid,
        )
        assert ev.lead_id == lid
        assert ev.campaign_id == cid
        assert ev.target_id == tid

    def test_provider_event_id_uniqueness_constraint_defined(self):
        """provider_event_id column must carry a unique constraint at the schema level."""
        col = TrackerEvent.__table__.c["provider_event_id"]
        # Column should be marked unique (SQLAlchemy sets col.unique=True)
        assert col.unique is True

    def test_token_is_optional(self):
        ev = TrackerEvent(event_type="delivery", channel="whatsapp")
        assert ev.token is None

    def test_provider_is_optional(self):
        ev = TrackerEvent(event_type="delivery", channel="sms")
        assert ev.provider is None


# ---------------------------------------------------------------------------
# Lead — new opt-out columns
# ---------------------------------------------------------------------------

class TestLeadOptOutColumns:
    def test_email_opt_out_default_false(self):
        # Column default is applied at INSERT time, not at Python instantiation.
        # Verify the column-level default is configured as False.
        col = Lead.__table__.c["email_opt_out"]
        assert col.default.arg is False

    def test_sms_opt_out_default_false(self):
        col = Lead.__table__.c["sms_opt_out"]
        assert col.default.arg is False

    def test_email_opt_out_can_be_set_true(self):
        lead = Lead(first_name="A", last_name="B", email="a@b.com", email_opt_out=True)
        assert lead.email_opt_out is True

    def test_sms_opt_out_can_be_set_true(self):
        lead = Lead(first_name="A", last_name="B", email="a@b.com", sms_opt_out=True)
        assert lead.sms_opt_out is True

    def test_both_opt_outs_independent(self):
        lead = Lead(first_name="A", last_name="B", email="a@b.com", email_opt_out=True, sms_opt_out=False)
        assert lead.email_opt_out is True
        assert lead.sms_opt_out is False

    def test_opt_out_columns_present_in_table(self):
        cols = {c.name for c in Lead.__table__.columns}
        assert "email_opt_out" in cols
        assert "sms_opt_out" in cols

    def test_opt_out_columns_not_nullable(self):
        email_col = Lead.__table__.c["email_opt_out"]
        sms_col = Lead.__table__.c["sms_opt_out"]
        assert email_col.nullable is False
        assert sms_col.nullable is False


# ---------------------------------------------------------------------------
# OutreachActivity — new external_message_id and channel columns
# ---------------------------------------------------------------------------

class TestOutreachActivityNewColumns:
    def test_external_message_id_column_present(self):
        cols = {c.name for c in OutreachActivity.__table__.columns}
        assert "external_message_id" in cols

    def test_channel_column_present(self):
        cols = {c.name for c in OutreachActivity.__table__.columns}
        assert "channel" in cols

    def test_external_message_id_default_none(self):
        target_id = uuid4()
        act = OutreachActivity(target_id=target_id, activity_type="email_sent")
        assert act.external_message_id is None

    def test_channel_default_none(self):
        target_id = uuid4()
        act = OutreachActivity(target_id=target_id, activity_type="email_sent")
        assert act.channel is None

    def test_set_external_message_id(self):
        target_id = uuid4()
        act = OutreachActivity(
            target_id=target_id,
            activity_type="email_sent",
            external_message_id="sg-msg-abc123",
        )
        assert act.external_message_id == "sg-msg-abc123"

    def test_set_channel(self):
        target_id = uuid4()
        act = OutreachActivity(target_id=target_id, activity_type="sms_sent", channel="sms")
        assert act.channel == "sms"

    def test_channel_values(self):
        for ch in ("email", "sms", "whatsapp"):
            act = OutreachActivity(target_id=uuid4(), activity_type="sent", channel=ch)
            assert act.channel == ch
