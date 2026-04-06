"""Tests for the leads API router."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from src.api.schemas import LeadCreate, LeadUpdate


@pytest.fixture
def sample_lead_payload():
    return {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@example.com",
        "company": "Joe's Pizza",
        "business_type": "Independent Restaurant",
        "locations": "1",
        "interest": "Branded Direct Ordering",
        "message": "Interested in switching from DoorDash",
        "source": "website_demo",
        "utm_source": "google",
        "utm_medium": "cpc",
        "utm_campaign": "restaurant-direct",
    }


class TestLeadSchemas:
    """Test Pydantic schema validation."""

    def test_lead_create_full(self, sample_lead_payload):
        lead = LeadCreate(**sample_lead_payload)
        assert lead.first_name == "John"
        assert lead.email == "john@example.com"
        assert lead.source == "website_demo"
        assert lead.utm_source == "google"

    def test_lead_create_minimal(self):
        lead = LeadCreate(first_name="Jane", last_name="Doe", email="jane@example.com")
        assert lead.source == "website_demo"
        assert lead.company is None
        assert lead.utm_source is None

    def test_lead_create_newsletter(self):
        lead = LeadCreate(
            first_name="Sub", last_name="Scriber",
            email="subscriber@example.com", source="website_newsletter",
        )
        assert lead.source == "website_newsletter"

    def test_lead_update_status(self):
        update = LeadUpdate(status="contacted")
        data = update.model_dump(exclude_unset=True)
        assert data == {"status": "contacted"}

    def test_lead_update_hubspot(self):
        update = LeadUpdate(hubspot_contact_id="hs-123", hubspot_deal_id="deal-456")
        data = update.model_dump(exclude_unset=True)
        assert "hubspot_contact_id" in data
        assert "hubspot_deal_id" in data

    def test_invalid_email_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LeadCreate(first_name="T", last_name="U", email="not-an-email")

    def test_empty_first_name_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LeadCreate(first_name="", last_name="U", email="t@example.com")

    def test_invalid_source_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LeadCreate(first_name="T", last_name="U", email="t@example.com", source="invalid_source")

    def test_invalid_status_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LeadUpdate(status="xyzzy_invalid")


class TestLeadModel:
    """Test Lead ORM model."""

    def test_lead_table_name(self):
        from src.db.models import Lead
        assert Lead.__tablename__ == "leads"

    def test_lead_explicit_status(self):
        from src.db.models import Lead
        lead = Lead(first_name="Test", last_name="User", email="test@example.com", status="new")
        assert lead.status == "new"

    def test_lead_explicit_source(self):
        from src.db.models import Lead
        lead = Lead(first_name="Test", last_name="User", email="test@example.com", source="website_demo")
        assert lead.source == "website_demo"

    def test_lead_nullable_fks(self):
        from src.db.models import Lead
        lead = Lead(first_name="Test", last_name="User", email="test@example.com")
        assert lead.restaurant_id is None
        assert lead.icp_score_id is None
        assert lead.icp_fit_label is None


class TestLeadRouterUnit:
    """Unit tests for lead router functions."""

    def test_valid_sources(self):
        valid_sources = ["website_demo", "website_newsletter", "website_partner", "manual"]
        for source in valid_sources:
            lead = LeadCreate(first_name="Test", last_name="User", email="test@example.com", source=source)
            assert lead.source == source

    def test_valid_statuses(self):
        valid_statuses = ["new", "contacted", "demo_scheduled", "pilot", "won", "lost"]
        for status in valid_statuses:
            update = LeadUpdate(status=status)
            assert update.status == status
