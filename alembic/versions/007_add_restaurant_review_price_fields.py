"""Add review_count, rating_avg, price_tier to restaurants table.

Revision ID: 007
Revises: 006
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("restaurants", sa.Column("rating_avg", sa.Float()))
    op.add_column("restaurants", sa.Column("review_count", sa.Integer(), server_default="0"))
    op.add_column("restaurants", sa.Column("price_tier", sa.Text()))


def downgrade() -> None:
    op.drop_column("restaurants", "price_tier")
    op.drop_column("restaurants", "review_count")
    op.drop_column("restaurants", "rating_avg")
