"""Initial schema.

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Restaurants table
    op.create_table(
        "restaurants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("address", sa.Text()),
        sa.Column("city", sa.Text()),
        sa.Column("state", sa.String(2)),
        sa.Column("zip_code", sa.String(10)),
        sa.Column("lat", sa.Float()),
        sa.Column("lng", sa.Float()),
        sa.Column("phone", sa.Text()),
        sa.Column("website", sa.Text()),
        sa.Column("cuisine_type", ARRAY(sa.Text()), server_default="{}"),
        sa.Column("is_chain", sa.Boolean(), server_default="false"),
        sa.Column("chain_name", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("name", "address", name="uq_restaurant_name_address"),
    )
    op.create_index("idx_restaurants_name", "restaurants", ["name"])
    op.create_index("idx_restaurants_city", "restaurants", ["city"])
    op.create_index("idx_restaurants_zip", "restaurants", ["zip_code"])

    # Source records table
    op.create_table(
        "source_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("raw_data", JSONB()),
        sa.Column("extracted_data", JSONB()),
        sa.Column("crawled_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_source_records_restaurant", "source_records", ["restaurant_id"])

    # ICP scores table
    op.create_table(
        "icp_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("restaurant_id", UUID(as_uuid=True), sa.ForeignKey("restaurants.id"), unique=True, nullable=False),
        sa.Column("is_independent", sa.Boolean()),
        sa.Column("has_delivery", sa.Boolean()),
        sa.Column("delivery_platforms", ARRAY(sa.Text()), server_default="{}"),
        sa.Column("has_pos", sa.Boolean()),
        sa.Column("pos_provider", sa.Text()),
        sa.Column("geo_density_score", sa.Float(), server_default="0"),
        sa.Column("review_volume", sa.Integer(), server_default="0"),
        sa.Column("rating_avg", sa.Float(), server_default="0"),
        sa.Column("total_icp_score", sa.Float(), server_default="0"),
        sa.Column("fit_label", sa.String(20), server_default="'unknown'"),
        sa.Column("scoring_version", sa.Integer(), server_default="1"),
        sa.Column("scored_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_icp_scores_total", "icp_scores", ["total_icp_score"])

    # Crawl jobs table
    op.create_table(
        "crawl_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("query", sa.Text()),
        sa.Column("location", sa.Text()),
        sa.Column("status", sa.String(20), server_default="'pending'"),
        sa.Column("total_items", sa.Integer(), server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_crawl_jobs_status", "crawl_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("crawl_jobs")
    op.drop_table("icp_scores")
    op.drop_table("source_records")
    op.drop_table("restaurants")
