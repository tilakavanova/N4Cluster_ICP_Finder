"""Add ICP scoring v2 fields to icp_scores table.

Revision ID: 006
Revises: 005
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("icp_scores", sa.Column("delivery_platform_count", sa.Integer(), server_default="0"))
    op.add_column("icp_scores", sa.Column("volume_proxy", sa.Float(), server_default="0.0"))
    op.add_column("icp_scores", sa.Column("cuisine_fit", sa.Float(), server_default="1.0"))
    op.add_column("icp_scores", sa.Column("price_tier", sa.Text()))
    op.add_column("icp_scores", sa.Column("price_point_fit", sa.Float(), server_default="0.7"))
    op.add_column("icp_scores", sa.Column("engagement_recency", sa.Float(), server_default="0.3"))
    op.add_column("icp_scores", sa.Column("disqualifier_penalty", sa.Float(), server_default="0.0"))


def downgrade() -> None:
    op.drop_column("icp_scores", "disqualifier_penalty")
    op.drop_column("icp_scores", "engagement_recency")
    op.drop_column("icp_scores", "price_point_fit")
    op.drop_column("icp_scores", "price_tier")
    op.drop_column("icp_scores", "cuisine_fit")
    op.drop_column("icp_scores", "volume_proxy")
    op.drop_column("icp_scores", "delivery_platform_count")
