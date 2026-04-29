"""Add A/B testing tables for template and scoring profile experiments (NIF-238, NIF-262).

Revision ID: 025
Revises: 024
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ab_experiments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False, index=True),
        sa.Column("experiment_type", sa.String(30), nullable=False, server_default="template"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("variants", JSONB, nullable=False, server_default="[]"),
        sa.Column("metric", sa.String(30), nullable=False),
        sa.Column("sample_size", sa.Integer, nullable=False, server_default="100"),
        sa.Column("winner_variant", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "ab_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", UUID(as_uuid=True), sa.ForeignKey("ab_experiments.id"), nullable=False, index=True),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("variant_name", sa.Text, nullable=False),
        sa.Column("outcome", JSONB),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("experiment_id", "lead_id", name="uq_ab_assignment_experiment_lead"),
    )


def downgrade() -> None:
    op.drop_table("ab_assignments")
    op.drop_table("ab_experiments")
