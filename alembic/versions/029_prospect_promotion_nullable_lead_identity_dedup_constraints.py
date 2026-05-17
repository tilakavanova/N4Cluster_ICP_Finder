"""prospect promotion: nullable lead identity, dedup constraints (NIF-284).

Relaxes lead identity NOT NULL constraints (promoted prospects have no
contact info yet) and adds partial unique on leads.restaurant_id plus
unique on outreach_targets(campaign_id, restaurant_id) for promotion
idempotency.

Descends from 028_merge_heads, which is the no-op mergepoint that resolves
the parallel-head split between 026 and 027 (introduced on master before
this branch was rebased).

Revision ID: 029
Revises: 028
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa


revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Relax non-null on lead identity fields (prospects have no contact yet)
    op.alter_column("leads", "first_name", existing_type=sa.Text(), nullable=True)
    op.alter_column("leads", "last_name", existing_type=sa.Text(), nullable=True)
    op.alter_column("leads", "email", existing_type=sa.Text(), nullable=True)

    # 2. Partial unique on leads.restaurant_id — dedup + lookup index
    op.create_index(
        "uq_leads_restaurant_id",
        "leads",
        ["restaurant_id"],
        unique=True,
        postgresql_where=sa.text("restaurant_id IS NOT NULL"),
    )

    # 3. Unique target per (campaign, restaurant)
    op.create_index(
        "uq_outreach_targets_campaign_restaurant",
        "outreach_targets",
        ["campaign_id", "restaurant_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_outreach_targets_campaign_restaurant", table_name="outreach_targets")
    op.drop_index("uq_leads_restaurant_id", table_name="leads")
    op.alter_column("leads", "email", existing_type=sa.Text(), nullable=False)
    op.alter_column("leads", "last_name", existing_type=sa.Text(), nullable=False)
    op.alter_column("leads", "first_name", existing_type=sa.Text(), nullable=False)
