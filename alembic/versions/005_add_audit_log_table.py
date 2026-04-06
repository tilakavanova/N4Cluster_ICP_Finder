"""Add audit_logs table for tracking cleanup and admin actions.

Revision ID: 005
Revises: 004
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("action", sa.String(50), nullable=False, index=True),
        sa.Column("entity_type", sa.String(30)),
        sa.Column("details", postgresql.JSONB(), server_default="{}"),
        sa.Column("performed_by", sa.Text(), server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
