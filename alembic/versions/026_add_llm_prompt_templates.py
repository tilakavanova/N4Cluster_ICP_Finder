"""Add LLM prompt templates table for versioned prompt management (NIF-264).

Revision ID: 026
Revises: 025
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_prompt_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("prompt_text", sa.Text, nullable=False),
        sa.Column("model_id", sa.String(50), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("temperature", sa.Float, nullable=False, server_default="0.7"),
        sa.Column("max_tokens", sa.Integer, nullable=False, server_default="4000"),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true", index=True),
        sa.Column("created_by", sa.Text, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("name", "version", name="uq_prompt_name_version"),
    )


def downgrade() -> None:
    op.drop_table("llm_prompt_templates")
