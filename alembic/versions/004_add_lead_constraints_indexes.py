"""Add lead status CHECK constraint and missing indexes.

Revision ID: 004
Revises: 003
Create Date: 2026-04-06
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NIF-177: Status enum constraint
    op.execute("""
        ALTER TABLE leads
        ADD CONSTRAINT ck_leads_status
        CHECK (status IN ('new', 'contacted', 'demo_scheduled', 'pilot', 'won', 'lost'))
    """)

    # NIF-177: Source enum constraint
    op.execute("""
        ALTER TABLE leads
        ADD CONSTRAINT ck_leads_source
        CHECK (source IN ('website_demo', 'website_newsletter', 'website_partner', 'manual'))
    """)

    # NIF-178: Missing indexes for common query patterns
    op.create_index("ix_leads_icp_fit_label", "leads", ["icp_fit_label"])
    op.create_index("ix_leads_source", "leads", ["source"])
    op.create_index("ix_leads_status_created", "leads", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_leads_status_created")
    op.drop_index("ix_leads_source")
    op.drop_index("ix_leads_icp_fit_label")
    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS ck_leads_source")
    op.execute("ALTER TABLE leads DROP CONSTRAINT IF EXISTS ck_leads_status")
