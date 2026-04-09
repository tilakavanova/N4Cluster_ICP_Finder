"""Add neighborhoods table for geo-intelligence (NIF-118,119,120,121).

Revision ID: 011
Revises: 010
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "neighborhoods",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("zip_code", sa.String(10), nullable=False, unique=True, index=True),
        sa.Column("name", sa.Text),
        sa.Column("city", sa.Text, index=True),
        sa.Column("state", sa.String(2), index=True),
        sa.Column("lat", sa.Float),
        sa.Column("lng", sa.Float),
        sa.Column("restaurant_count", sa.Integer, server_default="0"),
        sa.Column("avg_icp_score", sa.Float, server_default="0.0"),
        sa.Column("top_cuisines", ARRAY(sa.Text), server_default="{}"),
        sa.Column("independent_ratio", sa.Float, server_default="0.0"),
        sa.Column("delivery_coverage", sa.Float, server_default="0.0"),
        sa.Column("opportunity_score", sa.Float, server_default="0.0", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("neighborhoods")
