"""Add outreach orchestration & campaign engine tables (NIF-133 through NIF-136).

Revision ID: 015
Revises: 014
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outreach_campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False, index=True),
        sa.Column("campaign_type", sa.String(20), nullable=False, server_default="email"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("target_criteria", JSONB, server_default="{}"),
        sa.Column("start_date", sa.DateTime(timezone=True)),
        sa.Column("end_date", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Text, server_default="'system'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "outreach_targets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("outreach_campaigns.id"), nullable=False, index=True),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False, index=True),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=True, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("priority", sa.Integer, server_default="0"),
        sa.Column("assigned_to", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "outreach_activities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("target_id", UUID(as_uuid=True), sa.ForeignKey("outreach_targets.id"), nullable=False, index=True),
        sa.Column("activity_type", sa.String(30), nullable=False),
        sa.Column("outcome", sa.String(30)),
        sa.Column("notes", sa.Text),
        sa.Column("performed_by", sa.Text, server_default="'system'"),
        sa.Column("performed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "outreach_performance",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("outreach_campaigns.id"), unique=True, nullable=False, index=True),
        sa.Column("total_targets", sa.Integer, server_default="0"),
        sa.Column("contacted", sa.Integer, server_default="0"),
        sa.Column("responded", sa.Integer, server_default="0"),
        sa.Column("converted", sa.Integer, server_default="0"),
        sa.Column("response_rate", sa.Float, server_default="0.0"),
        sa.Column("conversion_rate", sa.Float, server_default="0.0"),
        sa.Column("last_calculated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("outreach_performance")
    op.drop_table("outreach_activities")
    op.drop_table("outreach_targets")
    op.drop_table("outreach_campaigns")
