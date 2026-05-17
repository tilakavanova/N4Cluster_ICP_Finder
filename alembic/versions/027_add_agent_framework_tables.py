"""Add agent framework, anomaly detection, and RLHF feedback tables (NIF-265-274).

Revision ID: 027
Revises: 025
Create Date: 2026-04-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers
revision = "027"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NIF-267: Campaign anomaly detection
    op.create_table(
        "campaign_anomaly_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("outreach_campaigns.id"), nullable=False, index=True),
        sa.Column("anomaly_type", sa.String(30), nullable=False, index=True),
        sa.Column("metric_value", sa.Float, nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("action_taken", sa.String(20), nullable=False, server_default="paused"),
        sa.Column("details", JSONB, server_default="{}"),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )

    # NIF-269: Agent runs
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(50), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("input_context", JSONB, server_default="{}"),
        sa.Column("output_result", JSONB, server_default="{}"),
        sa.Column("error_message", sa.Text),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # NIF-274: RLHF feedback
    op.create_table(
        "agent_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.String(50), nullable=False, index=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=True, index=True),
        sa.Column("input_context", JSONB, server_default="{}"),
        sa.Column("output_result", JSONB, server_default="{}"),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("feedback_text", sa.Text),
        sa.Column("rated_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_feedback")
    op.drop_table("agent_runs")
    op.drop_table("campaign_anomaly_logs")
