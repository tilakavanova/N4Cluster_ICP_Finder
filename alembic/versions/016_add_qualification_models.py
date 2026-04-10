"""Add AI qualification result and explanation tables (NIF-142, NIF-143).

Revision ID: 016
Revises: 015
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False, index=True),
        sa.Column("qualification_status", sa.String(20), nullable=False, server_default="'pending'", index=True),
        sa.Column("confidence_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("signals_summary", JSONB, nullable=False, server_default="[]"),
        sa.Column("qualified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("model_version", sa.String(20), nullable=False, server_default="'v1'"),
        sa.Column("reviewed_by", sa.Text),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_decision", sa.String(20)),
        sa.Column("review_notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "qualification_explanations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("result_id", UUID(as_uuid=True), sa.ForeignKey("qualification_results.id"), nullable=False, index=True),
        sa.Column("factor_name", sa.String(50), nullable=False),
        sa.Column("factor_value", sa.Text),
        sa.Column("impact", sa.String(10), nullable=False, server_default="'neutral'"),
        sa.Column("weight", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("explanation_text", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("qualification_explanations")
    op.drop_table("qualification_results")
