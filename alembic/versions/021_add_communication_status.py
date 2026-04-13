"""Add communication_status column to outreach_targets (NIF-222).

Revision ID: 021
Revises: 020
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa


revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outreach_targets",
        sa.Column(
            "communication_status",
            sa.String(20),
            nullable=False,
            server_default="queued",
        ),
    )
    op.create_index(
        "idx_ot_communication_status",
        "outreach_targets",
        ["communication_status"],
    )


def downgrade() -> None:
    op.drop_index("idx_ot_communication_status", table_name="outreach_targets")
    op.drop_column("outreach_targets", "communication_status")
