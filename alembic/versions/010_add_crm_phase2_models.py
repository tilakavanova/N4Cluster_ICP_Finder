"""Add CRM phase 2: account/contact history, follow-up tasks (NIF-70,71,112,115).

Revision ID: 010
Revises: 009
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Account history (NIF-70)
    op.create_table(
        "account_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("field_name", sa.String(50), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("changed_by", sa.Text, server_default="system"),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), index=True),
    )

    # Contact history (NIF-71)
    op.create_table(
        "contact_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey("contacts.id"), nullable=False, index=True),
        sa.Column("field_name", sa.String(50), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("changed_by", sa.Text, server_default="system"),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), index=True),
    )

    # Follow-up tasks (NIF-112)
    op.create_table(
        "follow_up_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("task_type", sa.String(30), nullable=False, server_default="follow_up"),
        sa.Column("priority", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("assigned_to", sa.Text),
        sa.Column("due_date", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # Add merged_into column for lead merge tracking (NIF-115)
    op.add_column("leads", sa.Column("merged_into_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=True))
    op.add_column("leads", sa.Column("is_merged", sa.Boolean, server_default="false"))


def downgrade() -> None:
    op.drop_column("leads", "is_merged")
    op.drop_column("leads", "merged_into_id")
    op.drop_table("follow_up_tasks")
    op.drop_table("contact_history")
    op.drop_table("account_history")
