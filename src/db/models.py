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
    rating_avg = Column(Float)
    review_count = Column(Integer, default=0)
    price_tier = Column(Text)
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
    lifecycle_stage = Column(String(30), nullable=False, default="new", index=True)
    owner = Column(Text)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True, index=True)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("contacts.id"), nullable=True)
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
    merged_into_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True)
    is_merged = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])
    icp_score = relationship("ICPScore", foreign_keys=[icp_score_id])
    account = relationship("Account", foreign_keys=[account_id])
    contact = relationship("Contact", foreign_keys=[contact_id])


class Account(Base):
    """Merchant business entity — groups leads and contacts."""
    __tablename__ = "accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False, index=True)
    business_type = Column(Text)
    location_count = Column(Integer, default=1)
    website = Column(Text)
    phone = Column(Text)
    city = Column(Text)
    state = Column(String(2))
    zip_code = Column(String(10))
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=True)
    icp_score_id = Column(UUID(as_uuid=True), ForeignKey("icp_scores.id"), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    contacts = relationship("Contact", back_populates="account", cascade="all, delete-orphan")
    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])


class Contact(Base):
    """Person associated with an account/merchant."""
    __tablename__ = "contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False, index=True)
    first_name = Column(Text, nullable=False)
    last_name = Column(Text, nullable=False)
    email = Column(Text, index=True)
    phone = Column(Text)
    role = Column(String(50))  # owner, manager, chef, etc.
    is_primary = Column(Boolean, default=False)
    confidence = Column(Float)  # how confident are we in this contact info
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    account = relationship("Account", back_populates="contacts")


class LeadStageHistory(Base):
    """Track lead lifecycle stage transitions."""
    __tablename__ = "lead_stage_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    from_stage = Column(String(30))
    to_stage = Column(String(30), nullable=False)
    changed_by = Column(Text, default="system")
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    lead = relationship("Lead", foreign_keys=[lead_id])


class LeadAssignmentHistory(Base):
    """Track lead owner assignment changes."""
    __tablename__ = "lead_assignment_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    from_owner = Column(Text)
    to_owner = Column(Text, nullable=False)
    changed_by = Column(Text, default="system")
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    lead = relationship("Lead", foreign_keys=[lead_id])


class AccountHistory(Base):
    """Track account field changes (NIF-70)."""
    __tablename__ = "account_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False, index=True)
    field_name = Column(String(50), nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    changed_by = Column(Text, default="system")
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    account = relationship("Account", foreign_keys=[account_id])


class ContactHistory(Base):
    """Track contact field changes (NIF-71)."""
    __tablename__ = "contact_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("contacts.id"), nullable=False, index=True)
    field_name = Column(String(50), nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    changed_by = Column(Text, default="system")
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    contact = relationship("Contact", foreign_keys=[contact_id])


class FollowUpTask(Base):
    """Follow-up tasks linked to leads (NIF-112)."""
    __tablename__ = "follow_up_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False, index=True)
    title = Column(Text, nullable=False)
    description = Column(Text)
    task_type = Column(String(30), nullable=False, default="follow_up")
    priority = Column(String(10), nullable=False, default="medium")
    status = Column(String(20), nullable=False, default="pending")
    assigned_to = Column(Text)
    due_date = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    lead = relationship("Lead", foreign_keys=[lead_id])


class Neighborhood(Base):
    """Normalized neighborhood boundary (NIF-118)."""
    __tablename__ = "neighborhoods"
    __table_args__ = (
        UniqueConstraint("zip_code", name="uq_neighborhood_zip"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zip_code = Column(String(10), nullable=False, index=True)
    name = Column(Text)  # human-friendly name, e.g. "Midtown Manhattan"
    city = Column(Text, index=True)
    state = Column(String(2), index=True)
    lat = Column(Float)  # centroid latitude
    lng = Column(Float)  # centroid longitude
    restaurant_count = Column(Integer, default=0)
    avg_icp_score = Column(Float, default=0.0)
    top_cuisines = Column(ARRAY(Text), default=list)
    independent_ratio = Column(Float, default=0.0)
    delivery_coverage = Column(Float, default=0.0)
    opportunity_score = Column(Float, default=0.0, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class RestaurantChange(Base):
    __tablename__ = "restaurant_changes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    change_type = Column(String(30), nullable=False, index=True)  # new_restaurant, rating_change, delivery_change, field_update
    field_name = Column(String(50))
    old_value = Column(Text)
    new_value = Column(Text)
    source = Column(String(20))
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])


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
