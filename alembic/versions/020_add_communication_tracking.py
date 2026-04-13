"""Add communication tracking tables and columns (NIF-221).

Revision ID: 020
Revises: 019
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracker_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("token", sa.Text),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("outreach_campaigns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_id", UUID(as_uuid=True), sa.ForeignKey("outreach_targets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.Text),
        sa.Column("provider_event_id", sa.Text, unique=True),
        sa.Column("event_metadata", JSONB),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index("idx_te_lead_occurred", "tracker_events", ["lead_id", sa.text("occurred_at DESC")])
    op.create_index("idx_te_campaign_occurred", "tracker_events", ["campaign_id", sa.text("occurred_at DESC")])
    op.create_index("idx_te_type_channel", "tracker_events", ["event_type", "channel"])
    op.create_index("idx_te_occurred", "tracker_events", [sa.text("occurred_at DESC")])

    op.add_column("outreach_activities", sa.Column("external_message_id", sa.Text))
    op.add_column("outreach_activities", sa.Column("channel", sa.Text))

    op.add_column("leads", sa.Column("email_opt_out", sa.Boolean, nullable=False, server_default=sa.text("FALSE")))
    op.add_column("leads", sa.Column("sms_opt_out", sa.Boolean, nullable=False, server_default=sa.text("FALSE")))


def downgrade() -> None:
    op.drop_column("leads", "sms_opt_out")
    op.drop_column("leads", "email_opt_out")
    op.drop_column("outreach_activities", "channel")
    op.drop_column("outreach_activities", "external_message_id")

    op.drop_index("idx_te_occurred", table_name="tracker_events")
    op.drop_index("idx_te_type_channel", table_name="tracker_events")
    op.drop_index("idx_te_campaign_occurred", table_name="tracker_events")
    op.drop_index("idx_te_lead_occurred", table_name="tracker_events")
    op.drop_table("tracker_events")
