"""Add cluster engine tables (NIF-151 through NIF-159).

Revision ID: 019
Revises: 018
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY


revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchant_clusters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False, index=True),
        sa.Column("cluster_type", sa.String(20), nullable=False, server_default="geographic"),
        sa.Column("zip_codes", ARRAY(sa.Text), server_default="{}"),
        sa.Column("center_lat", sa.Float),
        sa.Column("center_lng", sa.Float),
        sa.Column("radius_miles", sa.Float, server_default="1.0"),
        sa.Column("restaurant_count", sa.Integer, server_default="0"),
        sa.Column("avg_icp_score", sa.Float, server_default="0.0"),
        sa.Column("flywheel_score", sa.Float, server_default="0.0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="detected", index=True),
        sa.Column("detection_params", JSONB, server_default="{}"),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "cluster_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("merchant_clusters.id"), nullable=False, index=True),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("icp_score_at_join", sa.Float, server_default="0.0"),
        sa.UniqueConstraint("cluster_id", "restaurant_id", name="uq_cluster_member"),
    )

    op.create_table(
        "cluster_expansion_plans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("merchant_clusters.id"), nullable=False, index=True),
        sa.Column("target_restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False, index=True),
        sa.Column("sequence_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("strategy", sa.Text),
        sa.Column("priority_score", sa.Float, server_default="0.0", index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned", index=True),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "cluster_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("merchant_clusters.id"), nullable=False, index=True),
        sa.Column("event_type", sa.String(30), nullable=False, index=True),
        sa.Column("details", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "cluster_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("merchant_clusters.id"), nullable=False, index=True),
        sa.Column("feedback_type", sa.String(30), nullable=False, index=True),
        sa.Column("details", JSONB, server_default="{}"),
        sa.Column("submitted_by", sa.Text, server_default="'system'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("cluster_feedback")
    op.drop_table("cluster_history")
    op.drop_table("cluster_expansion_plans")
    op.drop_table("cluster_members")
    op.drop_table("merchant_clusters")
