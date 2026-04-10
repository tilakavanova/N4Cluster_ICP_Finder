"""Add conversion events and funnel tables (NIF-148, NIF-149).

Revision ID: 018
Revises: 017
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversion_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False, index=True),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=True, index=True),
        sa.Column("event_type", sa.String(30), nullable=False, index=True),
        sa.Column("source", sa.Text),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), index=True),
    )

    op.create_table(
        "conversion_funnels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("period", sa.Text, nullable=False, index=True),
        sa.Column("zip_code", sa.String(10), nullable=True, index=True),
        sa.Column("discovered", sa.Integer, server_default="0"),
        sa.Column("contacted", sa.Integer, server_default="0"),
        sa.Column("demo_scheduled", sa.Integer, server_default="0"),
        sa.Column("pilot_started", sa.Integer, server_default="0"),
        sa.Column("converted", sa.Integer, server_default="0"),
        sa.Column("churned", sa.Integer, server_default="0"),
        sa.Column("conversion_rate", sa.Float, server_default="0.0"),
        sa.Column("avg_days_to_convert", sa.Float, server_default="0.0"),
        sa.Column("last_calculated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("period", "zip_code", name="uq_conversion_funnel_period_zip"),
    )


def downgrade() -> None:
    op.drop_table("conversion_funnels")
    op.drop_table("conversion_events")
