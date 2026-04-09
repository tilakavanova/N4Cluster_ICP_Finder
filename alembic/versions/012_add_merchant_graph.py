"""Add merchant graph entity and relationship tables (NIF-122,123,124).

Revision ID: 012
Revises: 011
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchant_entities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), unique=True, nullable=False, index=True),
        sa.Column("entity_type", sa.String(30), nullable=False, server_default="restaurant"),
        sa.Column("tags", ARRAY(sa.Text), server_default="{}"),
        sa.Column("enrichment_data", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "merchant_relationships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_entity_id", UUID(as_uuid=True), sa.ForeignKey("merchant_entities.id"), nullable=False, index=True),
        sa.Column("target_entity_id", UUID(as_uuid=True), sa.ForeignKey("merchant_entities.id"), nullable=False, index=True),
        sa.Column("relationship_type", sa.String(30), nullable=False),
        sa.Column("strength", sa.Float, server_default="1.0"),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("source_entity_id", "target_entity_id", "relationship_type", name="uq_merchant_rel"),
    )


def downgrade() -> None:
    op.drop_table("merchant_relationships")
    op.drop_table("merchant_entities")
