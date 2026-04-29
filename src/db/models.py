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
    communication_engagement = Column(Float)  # NIF-236: 9th signal, nullable (None = no data)
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
    email_opt_out = Column(Boolean, nullable=False, default=False)
    sms_opt_out = Column(Boolean, nullable=False, default=False)
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


class MerchantEntity(Base):
    """Merchant graph entity node (NIF-122). Enriched view of a restaurant for graph queries."""
    __tablename__ = "merchant_entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), unique=True, nullable=False, index=True)
    entity_type = Column(String(30), nullable=False, default="restaurant")  # restaurant, chain_group, market
    tags = Column(ARRAY(Text), default=list)  # e.g. ["high-volume", "pizza", "independent"]
    enrichment_data = Column(JSONB, default=dict)  # arbitrary enrichment metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])


class MerchantRelationship(Base):
    """Edge between two merchant entities (NIF-123)."""
    __tablename__ = "merchant_relationships"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "relationship_type", name="uq_merchant_rel"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_entity_id = Column(UUID(as_uuid=True), ForeignKey("merchant_entities.id"), nullable=False, index=True)
    target_entity_id = Column(UUID(as_uuid=True), ForeignKey("merchant_entities.id"), nullable=False, index=True)
    relationship_type = Column(String(30), nullable=False)  # same_cuisine, same_neighborhood, same_chain, competitor, cluster_peer
    strength = Column(Float, default=1.0)  # 0.0-1.0 edge weight
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    source = relationship("MerchantEntity", foreign_keys=[source_entity_id])
    target = relationship("MerchantEntity", foreign_keys=[target_entity_id])


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


class ScoringProfile(Base):
    """Configurable scoring profile (NIF-125)."""
    __tablename__ = "scoring_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True, index=True)
    version = Column(Integer, nullable=False, default=1)
    description = Column(Text)
    signals = Column(JSONB, nullable=False, default=list)  # [{name, weight, type, enabled}]
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    rules = relationship("ScoringRule", back_populates="profile", cascade="all, delete-orphan")
    score_versions = relationship("ScoreVersion", back_populates="profile", cascade="all, delete-orphan")
    config_links = relationship("ScoringConfigLink", back_populates="profile", cascade="all, delete-orphan")


class ScoringRule(Base):
    """Rule within a scoring profile (NIF-126)."""
    __tablename__ = "scoring_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("scoring_profiles.id"), nullable=False, index=True)
    signal_name = Column(String(50), nullable=False)
    rule_type = Column(String(20), nullable=False)  # threshold, range, boolean, custom
    condition = Column(JSONB, nullable=False, default=dict)  # e.g. {"min": 50, "max": 200}
    points = Column(Float, nullable=False, default=0.0)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    profile = relationship("ScoringProfile", back_populates="rules")


class ScoreExplanation(Base):
    """Detailed score breakdown for a restaurant (NIF-127, NIF-131)."""
    __tablename__ = "score_explanations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("scoring_profiles.id"), nullable=False, index=True)
    signal_breakdown = Column(JSONB, nullable=False, default=list)  # [{signal, raw_value, weighted_value, explanation}]
    total_score = Column(Float, nullable=False, default=0.0, index=True)
    fit_label = Column(String(20), nullable=False, default="unknown")
    explanation_text = Column(Text)
    scored_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])
    profile = relationship("ScoringProfile", foreign_keys=[profile_id])


class ScoreVersion(Base):
    """Version history for scoring profiles (NIF-129)."""
    __tablename__ = "score_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("scoring_profiles.id"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    changes = Column(JSONB, nullable=False, default=dict)  # {field: {old, new}}
    created_by = Column(Text, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    profile = relationship("ScoringProfile", back_populates="score_versions")


class ScoringConfigLink(Base):
    """Link a scoring profile to a market/cuisine/chain_group (NIF-130)."""
    __tablename__ = "scoring_config_links"
    __table_args__ = (
        UniqueConstraint("profile_id", "entity_type", "entity_value", name="uq_scoring_config_link"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("scoring_profiles.id"), nullable=False, index=True)
    entity_type = Column(String(30), nullable=False)  # market, cuisine, chain_group
    entity_value = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    profile = relationship("ScoringProfile", back_populates="config_links")


class ScoreRecalcJob(Base):
    """Batch score recalculation job (NIF-132)."""
    __tablename__ = "score_recalc_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("scoring_profiles.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending, running, completed, failed
    total_items = Column(Integer, default=0)
    processed_items = Column(Integer, default=0)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    profile = relationship("ScoringProfile", foreign_keys=[profile_id])


class QualificationResult(Base):
    """AI merchant qualification result (NIF-142)."""
    __tablename__ = "qualification_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    qualification_status = Column(String(20), nullable=False, default="pending", index=True)  # qualified, not_qualified, needs_review, pending
    confidence_score = Column(Float, nullable=False, default=0.0)  # 0.0-1.0
    signals_summary = Column(JSONB, nullable=False, default=list)  # array of evaluated signals
    qualified_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    model_version = Column(String(20), nullable=False, default="v1")
    reviewed_by = Column(Text)
    reviewed_at = Column(DateTime(timezone=True))
    review_decision = Column(String(20))  # approved, rejected
    review_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])
    explanations = relationship("QualificationExplanation", back_populates="result", cascade="all, delete-orphan")


class QualificationExplanation(Base):
    """Qualification factor explanation (NIF-143)."""
    __tablename__ = "qualification_explanations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id = Column(UUID(as_uuid=True), ForeignKey("qualification_results.id"), nullable=False, index=True)
    factor_name = Column(String(50), nullable=False)
    factor_value = Column(Text)
    impact = Column(String(10), nullable=False, default="neutral")  # positive, negative, neutral
    weight = Column(Float, nullable=False, default=0.0)
    explanation_text = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    result = relationship("QualificationResult", back_populates="explanations")


class ConfigRegistry(Base):
    """Configuration registry entry (NIF-137)."""
    __tablename__ = "config_registry"
    __table_args__ = (
        UniqueConstraint("namespace", "key", name="uq_config_namespace_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String(50), nullable=False, index=True)
    key = Column(String(100), nullable=False, index=True)
    value = Column(JSONB, nullable=False, default=dict)
    description = Column(Text)
    data_type = Column(String(20), nullable=False, default="string")  # string, int, float, bool, json
    is_secret = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    versions = relationship("ConfigVersion", back_populates="config", cascade="all, delete-orphan")
    overrides = relationship("ConfigOverride", back_populates="config", cascade="all, delete-orphan")


class ConfigVersion(Base):
    """Configuration version history (NIF-138)."""
    __tablename__ = "config_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_id = Column(UUID(as_uuid=True), ForeignKey("config_registry.id"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    old_value = Column(JSONB)
    new_value = Column(JSONB, nullable=False)
    changed_by = Column(Text, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    config = relationship("ConfigRegistry", back_populates="versions")


class ConfigOverride(Base):
    """Market/scope-specific configuration override (NIF-139)."""
    __tablename__ = "config_overrides"
    __table_args__ = (
        UniqueConstraint("config_id", "scope_type", "scope_value", name="uq_config_override_scope"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_id = Column(UUID(as_uuid=True), ForeignKey("config_registry.id"), nullable=False, index=True)
    scope_type = Column(String(30), nullable=False)  # market, cuisine, region
    scope_value = Column(Text, nullable=False)
    override_value = Column(JSONB, nullable=False)
    priority = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    config = relationship("ConfigRegistry", back_populates="overrides")


class OutreachCampaign(Base):
    """Outreach campaign definition (NIF-133)."""
    __tablename__ = "outreach_campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False, index=True)
    campaign_type = Column(String(20), nullable=False, default="email")
    status = Column(String(20), nullable=False, default="draft", index=True)
    target_criteria = Column(JSONB, default=dict)
    start_date = Column(DateTime(timezone=True))
    end_date = Column(DateTime(timezone=True))
    created_by = Column(Text, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    targets = relationship("OutreachTarget", back_populates="campaign", cascade="all, delete-orphan")
    performance = relationship("OutreachPerformance", back_populates="campaign", uselist=False, cascade="all, delete-orphan")


class OutreachTarget(Base):
    """Individual outreach target within a campaign (NIF-134)."""
    __tablename__ = "outreach_targets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("outreach_campaigns.id"), nullable=False, index=True)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    communication_status = Column(String(20), nullable=False, default="queued", index=True)
    priority = Column(Integer, default=0)
    assigned_to = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    campaign = relationship("OutreachCampaign", back_populates="targets")
    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])
    lead = relationship("Lead", foreign_keys=[lead_id])
    activities = relationship("OutreachActivity", back_populates="target", cascade="all, delete-orphan")


class OutreachActivity(Base):
    """Activity log entry for an outreach target (NIF-135)."""
    __tablename__ = "outreach_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id = Column(UUID(as_uuid=True), ForeignKey("outreach_targets.id"), nullable=False, index=True)
    activity_type = Column(String(30), nullable=False)
    outcome = Column(String(30))
    notes = Column(Text)
    performed_by = Column(Text, default="system")
    performed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    external_message_id = Column(Text)
    channel = Column(Text)  # email|sms|whatsapp

    target = relationship("OutreachTarget", back_populates="activities")


class OutreachPerformance(Base):
    """Aggregated performance summary for a campaign (NIF-136)."""
    __tablename__ = "outreach_performance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("outreach_campaigns.id"), unique=True, nullable=False, index=True)
    total_targets = Column(Integer, default=0)
    contacted = Column(Integer, default=0)
    responded = Column(Integer, default=0)
    converted = Column(Integer, default=0)
    response_rate = Column(Float, default=0.0)
    conversion_rate = Column(Float, default=0.0)
    last_calculated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    campaign = relationship("OutreachCampaign", back_populates="performance")


class RepQueueItem(Base):
    """Sales rep work queue item (NIF-145)."""
    __tablename__ = "rep_queue_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rep_id = Column(Text, nullable=False, index=True)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True, index=True)
    priority_score = Column(Float, nullable=False, default=0.0, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending, claimed, completed, skipped
    reason = Column(Text)  # why this item is in the queue
    context_data = Column(JSONB, default=dict)  # ICP score, fit label, last activity, etc.
    claimed_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])
    lead = relationship("Lead", foreign_keys=[lead_id])


class RepQueueRanking(Base):
    """Sales rep performance ranking (NIF-146)."""
    __tablename__ = "rep_queue_rankings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rep_id = Column(Text, nullable=False, unique=True, index=True)
    total_items = Column(Integer, default=0)
    completed_today = Column(Integer, default=0)
    avg_completion_time_mins = Column(Float, default=0.0)
    active_items = Column(Integer, default=0)
    last_activity_at = Column(DateTime(timezone=True))
    ranking_score = Column(Float, default=0.0, index=True)  # performance ranking
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ConversionEvent(Base):
    """Conversion funnel event (NIF-148)."""
    __tablename__ = "conversion_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True, index=True)
    event_type = Column(String(30), nullable=False, index=True)  # discovered, contacted, demo_scheduled, pilot_started, converted, churned
    source = Column(Text)
    metadata_ = Column("metadata", JSONB, default=dict)
    occurred_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])
    lead = relationship("Lead", foreign_keys=[lead_id])


class ConversionFunnel(Base):
    """Aggregated conversion funnel summary (NIF-149)."""
    __tablename__ = "conversion_funnels"
    __table_args__ = (
        UniqueConstraint("period", "zip_code", name="uq_conversion_funnel_period_zip"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    period = Column(Text, nullable=False, index=True)  # e.g. "2026-W15", "2026-04"
    zip_code = Column(String(10), nullable=True, index=True)
    discovered = Column(Integer, default=0)
    contacted = Column(Integer, default=0)
    demo_scheduled = Column(Integer, default=0)
    pilot_started = Column(Integer, default=0)
    converted = Column(Integer, default=0)
    churned = Column(Integer, default=0)
    conversion_rate = Column(Float, default=0.0)
    avg_days_to_convert = Column(Float, default=0.0)
    last_calculated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MerchantCluster(Base):
    """Cluster of nearby merchants (NIF-151)."""
    __tablename__ = "merchant_clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False, index=True)
    cluster_type = Column(String(20), nullable=False, default="geographic")  # geographic, cuisine, chain
    zip_codes = Column(ARRAY(Text), default=list)
    center_lat = Column(Float)
    center_lng = Column(Float)
    radius_miles = Column(Float, default=1.0)
    restaurant_count = Column(Integer, default=0)
    avg_icp_score = Column(Float, default=0.0)
    flywheel_score = Column(Float, default=0.0)
    status = Column(String(20), nullable=False, default="detected", index=True)  # detected, active, expanding, mature
    detection_params = Column(JSONB, default=dict)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    members = relationship("ClusterMember", back_populates="cluster", cascade="all, delete-orphan")
    expansion_plans = relationship("ClusterExpansionPlan", back_populates="cluster", cascade="all, delete-orphan")
    history = relationship("ClusterHistory", back_populates="cluster", cascade="all, delete-orphan")
    feedback = relationship("ClusterFeedback", back_populates="cluster", cascade="all, delete-orphan")


class ClusterMember(Base):
    """Member of a merchant cluster (NIF-152)."""
    __tablename__ = "cluster_members"
    __table_args__ = (
        UniqueConstraint("cluster_id", "restaurant_id", name="uq_cluster_member"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("merchant_clusters.id"), nullable=False, index=True)
    restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False, default="member")  # anchor, member, prospect
    joined_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    icp_score_at_join = Column(Float, default=0.0)

    cluster = relationship("MerchantCluster", back_populates="members")
    restaurant = relationship("Restaurant", foreign_keys=[restaurant_id])


class ClusterExpansionPlan(Base):
    """Expansion plan for a cluster (NIF-153)."""
    __tablename__ = "cluster_expansion_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("merchant_clusters.id"), nullable=False, index=True)
    target_restaurant_id = Column(UUID(as_uuid=True), ForeignKey("restaurants.id"), nullable=False, index=True)
    sequence_order = Column(Integer, nullable=False, default=0)
    strategy = Column(Text)
    priority_score = Column(Float, default=0.0, index=True)
    status = Column(String(20), nullable=False, default="planned", index=True)  # planned, in_progress, completed, skipped
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    cluster = relationship("MerchantCluster", back_populates="expansion_plans")
    target_restaurant = relationship("Restaurant", foreign_keys=[target_restaurant_id])


class ClusterHistory(Base):
    """Cluster event history (NIF-158)."""
    __tablename__ = "cluster_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("merchant_clusters.id"), nullable=False, index=True)
    event_type = Column(String(30), nullable=False, index=True)  # detected, member_added, member_removed, recalculated, expanded, campaign_launched
    details = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    cluster = relationship("MerchantCluster", back_populates="history")


class ClusterFeedback(Base):
    """Cluster feedback entry (NIF-159)."""
    __tablename__ = "cluster_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("merchant_clusters.id"), nullable=False, index=True)
    feedback_type = Column(String(30), nullable=False, index=True)  # expansion_success, expansion_failure, quality_rating
    details = Column(JSONB, default=dict)
    submitted_by = Column(Text, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    cluster = relationship("MerchantCluster", back_populates="feedback")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(30))
    details = Column(JSONB, default=dict)
    performed_by = Column(Text, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class TrackerEvent(Base):
    """Communication delivery/engagement event from provider webhooks (NIF-221).

    Tracks opens, clicks, bounces, opt-outs etc. for email/SMS/WhatsApp.
    provider_event_id is UNIQUE to deduplicate webhook retries.
    """
    __tablename__ = "tracker_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(Text)
    event_type = Column(Text, nullable=False)  # open|click|delivery|read|bounce|complaint|unsubscribe|stop
    channel = Column(Text, nullable=False)  # email|sms|whatsapp
    lead_id = Column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("outreach_campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    target_id = Column(UUID(as_uuid=True), ForeignKey("outreach_targets.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = Column(Text)  # sendgrid|mailgun|plivo|twilio|meta
    provider_event_id = Column(Text, unique=True)
    event_metadata = Column(JSONB)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    lead = relationship("Lead", foreign_keys=[lead_id])
    campaign = relationship("OutreachCampaign", foreign_keys=[campaign_id])
    target = relationship("OutreachTarget", foreign_keys=[target_id])
