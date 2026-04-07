"""SQLAlchemy ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Restaurant(Base):
    __tablename__ = "restaurants"
    __table_args__ = (
        UniqueConstraint("name", "address", name="uq_restaurant_name_address"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False, index=True)
    address = Column(Text)
    city = Column(Text, index=True)
    state = Column(String(2))
    zip_code = Column(String(10), index=True)
    lat = Column(Float)
    lng = Column(Float)
    phone = Column(Text)
    website = Column(Text)
    cuisine_type = Column(ARRAY(Text), default=list)
    is_chain = Column(Boolean, default=False)
    chain_name = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    source_records = relationship("SourceRecord", back_populates="restaurant", cascade="all, delete-orphan")
    icp_score = relationship("ICPScore", back_populates="restaurant", uselist=False, cascade="all, delete-orphan")


class SourceRecord(Base):
    __tablename__ = "source_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    source = Column(String(20), nullable=False)
    source_url = Column(Text)
    raw_data = Column(JSONB)
    extracted_data = Column(JSONB)
    crawled_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    restaurant = relationship("Restaurant", back_populates="source_records")


class ICPScore(Base):
    __tablename__ = "icp_scores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), unique=True, nullable=False)
    is_independent = Column(Boolean)
    has_delivery = Column(Boolean)
    delivery_platforms = Column(ARRAY(Text), default=list)
    delivery_platform_count = Column(Integer, default=0)
    has_pos = Column(Boolean)
    pos_provider = Column(Text)
    geo_density_score = Column(Float, default=0.0)
    review_volume = Column(Integer, default=0)
    rating_avg = Column(Float, default=0.0)
    volume_proxy = Column(Float, default=0.0)
    cuisine_fit = Column(Float, default=1.0)
    price_tier = Column(Text)
    price_point_fit = Column(Float, default=0.7)
    engagement_recency = Column(Float, default=0.3)
    disqualifier_penalty = Column(Float, default=0.0)
    total_icp_score = Column(Float, default=0.0, index=True)
    fit_label = Column(String(20), default="unknown")
    scoring_version = Column(Integer, default=2)
    scored_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    restaurant = relationship("Restaurant", back_populates="icp_score")


class Lead(Base):
    __tablename__ = "leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name = Column(Text, nullable=False)
    last_name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, index=True)
    company = Column(Text)
    business_type = Column(Text)
    locations = Column(Text)
    interest = Column(Text)
    message = Column(Text)
    source = Column(String(30), nullable=False, default="website_demo")
    status = Column(String(20), nullable=False, default="new", index=True)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=True)
    icp_score_id = Column(UUID(as_uuid=True), ForeignKey("icp_scores.id"), nullable=True)
    icp_fit_label = Column(String(20))
    icp_total_score = Column(Float)
    matched_restaurant_name = Column(Text)
    match_confidence = Column(Float)
    is_independent = Column(Boolean)
    has_delivery = Column(Boolean)
    delivery_platforms = Column(ARRAY(Text), default=list)
    has_pos = Column(Boolean)
    pos_provider = Column(Text)
    geo_density_score = Column(Float)
    hubspot_contact_id = Column(Text)
    hubspot_deal_id = Column(Text)
    utm_source = Column(Text)
    utm_medium = Column(Text)
    utm_campaign = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])
    icp_score = relationship("ICPScore", foreign_keys=[icp_score_id])


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(20), nullable=False)
    query = Column(Text)
    location = Column(Text)
    status = Column(String(20), default="pending", index=True)
    total_items = Column(Integer, default=0)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(30))
    details = Column(JSONB, default=dict)
    performed_by = Column(Text, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
