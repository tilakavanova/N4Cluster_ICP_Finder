"""Add restaurant_changes table for change detection.

Revision ID: 008
Revises: 007
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "restaurant_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False, index=True),
        sa.Column("change_type", sa.String(30), nullable=False, index=True),
        sa.Column("field_name", sa.String(50)),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("source", sa.String(20)),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), index=True),
    )


def downgrade() -> None:
    op.drop_table("restaurant_changes")
