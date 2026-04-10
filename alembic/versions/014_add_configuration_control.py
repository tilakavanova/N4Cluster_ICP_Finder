"""Add configuration control center tables (NIF-137 through NIF-141).

Revision ID: 014
Revises: 013
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_registry",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("namespace", sa.String(50), nullable=False, index=True),
        sa.Column("key", sa.String(100), nullable=False, index=True),
        sa.Column("value", JSONB, nullable=False, server_default="{}"),
        sa.Column("description", sa.Text),
        sa.Column("data_type", sa.String(20), nullable=False, server_default="'string'"),
        sa.Column("is_secret", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("namespace", "key", name="uq_config_namespace_key"),
    )

    op.create_table(
        "config_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("config_id", UUID(as_uuid=True), sa.ForeignKey("config_registry.id"), nullable=False, index=True),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("old_value", JSONB),
        sa.Column("new_value", JSONB, nullable=False),
        sa.Column("changed_by", sa.Text, server_default="'system'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "config_overrides",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("config_id", UUID(as_uuid=True), sa.ForeignKey("config_registry.id"), nullable=False, index=True),
        sa.Column("scope_type", sa.String(30), nullable=False),
        sa.Column("scope_value", sa.Text, nullable=False),
        sa.Column("override_value", JSONB, nullable=False),
        sa.Column("priority", sa.Integer, server_default="0"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("config_id", "scope_type", "scope_value", name="uq_config_override_scope"),
    )


def downgrade() -> None:
    op.drop_table("config_overrides")
    op.drop_table("config_versions")
    op.drop_table("config_registry")
