"""Add rep queue item and ranking tables (NIF-145, NIF-146).

Revision ID: 017
Revises: 016
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rep_queue_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rep_id", sa.Text, nullable=False, index=True),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False, index=True),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=True, index=True),
        sa.Column("priority_score", sa.Float, nullable=False, server_default="0.0", index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="'pending'", index=True),
        sa.Column("reason", sa.Text),
        sa.Column("context_data", JSONB, server_default="{}"),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "rep_queue_rankings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rep_id", sa.Text, nullable=False, unique=True, index=True),
        sa.Column("total_items", sa.Integer, server_default="0"),
        sa.Column("completed_today", sa.Integer, server_default="0"),
        sa.Column("avg_completion_time_mins", sa.Float, server_default="0.0"),
        sa.Column("active_items", sa.Integer, server_default="0"),
        sa.Column("last_activity_at", sa.DateTime(timezone=True)),
        sa.Column("ranking_score", sa.Float, server_default="0.0", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("rep_queue_rankings")
    op.drop_table("rep_queue_items")
