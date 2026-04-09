"""Add CRM core models: accounts, contacts, lead stage/assignment history.

Revision ID: 009
Revises: 008
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Account entity
    op.create_table(
        "accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False, index=True),
        sa.Column("business_type", sa.Text),
        sa.Column("location_count", sa.Integer, default=1),
        sa.Column("website", sa.Text),
        sa.Column("phone", sa.Text),
        sa.Column("city", sa.Text),
        sa.Column("state", sa.String(2)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=True),
        sa.Column("icp_score_id", UUID(as_uuid=True), sa.ForeignKey("icp_scores.id"), nullable=True),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # Contact entity
    op.create_table(
        "contacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("first_name", sa.Text, nullable=False),
        sa.Column("last_name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, index=True),
        sa.Column("phone", sa.Text),
        sa.Column("role", sa.String(50)),
        sa.Column("is_primary", sa.Boolean, default=False),
        sa.Column("confidence", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # Lead stage history
    op.create_table(
        "lead_stage_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("from_stage", sa.String(30)),
        sa.Column("to_stage", sa.String(30), nullable=False),
        sa.Column("changed_by", sa.Text, server_default="system"),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), index=True),
    )

    # Lead assignment history
    op.create_table(
        "lead_assignment_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("from_owner", sa.Text),
        sa.Column("to_owner", sa.Text, nullable=False),
        sa.Column("changed_by", sa.Text, server_default="system"),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), index=True),
    )

    # Add new columns to leads table
    op.add_column("leads", sa.Column("lifecycle_stage", sa.String(30), nullable=True, server_default="new"))
    op.add_column("leads", sa.Column("owner", sa.Text, nullable=True))
    op.add_column("leads", sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=True))
    op.add_column("leads", sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey("contacts.id"), nullable=True))
    op.create_index("ix_leads_lifecycle_stage", "leads", ["lifecycle_stage"])
    op.create_index("ix_leads_account_id", "leads", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_leads_account_id", table_name="leads")
    op.drop_index("ix_leads_lifecycle_stage", table_name="leads")
    op.drop_column("leads", "contact_id")
    op.drop_column("leads", "account_id")
    op.drop_column("leads", "owner")
    op.drop_column("leads", "lifecycle_stage")
    op.drop_table("lead_assignment_history")
    op.drop_table("lead_stage_history")
    op.drop_table("contacts")
    op.drop_table("accounts")
