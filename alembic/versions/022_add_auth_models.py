"""Add API client and token tables for JWT/OAuth2 auth (NIF-254).

Revision ID: 022
Revises: 021
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_clients",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("client_secret_hash", sa.Text(), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="60"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_api_clients_client_id", "api_clients", ["client_id"], unique=True)

    op.create_table(
        "api_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_api_tokens_client_id", "api_tokens", ["client_id"])
    op.create_index("idx_api_tokens_token_hash", "api_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_index("idx_api_tokens_token_hash", table_name="api_tokens")
    op.drop_index("idx_api_tokens_client_id", table_name="api_tokens")
    op.drop_table("api_tokens")
    op.drop_index("idx_api_clients_client_id", table_name="api_clients")
    op.drop_table("api_clients")
