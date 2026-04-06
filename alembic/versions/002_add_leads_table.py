"""Add leads table for website lead management.

Revision ID: 002
Revises: 001
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pg_trgm extension for fuzzy matching
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False, index=True),
        sa.Column("company", sa.Text()),
        sa.Column("business_type", sa.Text()),
        sa.Column("locations", sa.Text()),
        sa.Column("interest", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.Column("source", sa.String(30), nullable=False, server_default="website_demo"),
        sa.Column("status", sa.String(20), nullable=False, server_default="new", index=True),
        sa.Column("restaurant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=True),
        sa.Column("icp_score_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("icp_scores.id"), nullable=True),
        sa.Column("icp_fit_label", sa.String(20)),
        sa.Column("icp_total_score", sa.Float()),
        sa.Column("matched_restaurant_name", sa.Text()),
        sa.Column("match_confidence", sa.Float()),
        sa.Column("is_independent", sa.Boolean()),
        sa.Column("has_delivery", sa.Boolean()),
        sa.Column("delivery_platforms", postgresql.ARRAY(sa.Text()), server_default="{}"),
        sa.Column("has_pos", sa.Boolean()),
        sa.Column("pos_provider", sa.Text()),
        sa.Column("geo_density_score", sa.Float()),
        sa.Column("hubspot_contact_id", sa.Text()),
        sa.Column("hubspot_deal_id", sa.Text()),
        sa.Column("utm_source", sa.Text()),
        sa.Column("utm_medium", sa.Text()),
        sa.Column("utm_campaign", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create index for fuzzy company matching
    op.execute("CREATE INDEX ix_leads_company_trgm ON leads USING gin (company gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_leads_company_trgm")
    op.drop_table("leads")
