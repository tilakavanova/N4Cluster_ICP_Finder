"""Add configurable scoring engine tables (NIF-125 through NIF-132).

Revision ID: 013
Revises: 012
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scoring_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("description", sa.Text),
        sa.Column("signals", JSONB, nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean, server_default="true", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "scoring_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("scoring_profiles.id"), nullable=False, index=True),
        sa.Column("signal_name", sa.String(50), nullable=False),
        sa.Column("rule_type", sa.String(20), nullable=False),
        sa.Column("condition", JSONB, nullable=False, server_default="{}"),
        sa.Column("points", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "score_explanations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False, index=True),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("scoring_profiles.id"), nullable=False, index=True),
        sa.Column("signal_breakdown", JSONB, nullable=False, server_default="[]"),
        sa.Column("total_score", sa.Float, nullable=False, server_default="0.0", index=True),
        sa.Column("fit_label", sa.String(20), nullable=False, server_default="'unknown'"),
        sa.Column("explanation_text", sa.Text),
        sa.Column("scored_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "score_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("scoring_profiles.id"), nullable=False, index=True),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("changes", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Text, server_default="'system'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "scoring_config_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("scoring_profiles.id"), nullable=False, index=True),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("entity_value", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("profile_id", "entity_type", "entity_value", name="uq_scoring_config_link"),
    )

    op.create_table(
        "score_recalc_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("scoring_profiles.id"), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="'pending'", index=True),
        sa.Column("total_items", sa.Integer, server_default="0"),
        sa.Column("processed_items", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("score_recalc_jobs")
    op.drop_table("scoring_config_links")
    op.drop_table("score_versions")
    op.drop_table("score_explanations")
    op.drop_table("scoring_rules")
    op.drop_table("scoring_profiles")
