"""SQLAlchemy ORM models for JWT/OAuth2 authentication (NIF-254)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from src.db.models import Base


class APIClient(Base):
    """Represents a registered API client (application) with credentials."""

    __tablename__ = "api_clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    client_id = Column(String(64), nullable=False, unique=True, index=True)
    client_secret_hash = Column(Text, nullable=False)
    scopes = Column(JSONB, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    rate_limit_per_minute = Column(Integer, nullable=False, default=60)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    tokens = relationship(
        "APIToken",
        back_populates="client",
        cascade="all, delete-orphan",
        foreign_keys="APIToken.client_id",
    )


class APIToken(Base):
    """Represents an issued JWT token (for revocation tracking)."""

    __tablename__ = "api_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(
        UUID(as_uuid=True),
        ForeignKey("api_clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(Text, nullable=False, index=True)
    scopes = Column(JSONB, nullable=False, default=list)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    client = relationship(
        "APIClient",
        back_populates="tokens",
        foreign_keys=[client_id],
    )
