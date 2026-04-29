"""Add communication_engagement column to icp_scores (NIF-236).

Revision ID: 023
Revises: 022
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa


revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "icp_scores",
        sa.Column("communication_engagement", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("icp_scores", "communication_engagement")
