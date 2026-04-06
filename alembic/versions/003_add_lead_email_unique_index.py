"""Add unique index on leads.email (case-insensitive).

Revision ID: 003
Revises: 002
Create Date: 2026-04-06
"""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX uq_leads_email_lower ON leads (LOWER(email))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_leads_email_lower")
