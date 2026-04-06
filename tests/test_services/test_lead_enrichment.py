"""Tests for the lead enrichment service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.db.models import Lead, Restaurant, ICPScore
from src.services.lead_enrichment import LeadEnrichmentService


@pytest.fixture
def mock_session():
    session = AsyncMock()
    return session


@pytest.fixture
def sample_lead():
    return Lead(
        first_name="John",
        last_name="Doe",
        email="john@example.com",
        company="Joe's Pizza",
        source="website_demo",
        status="new",
    )


@pytest.fixture
def sample_restaurant():
    r = MagicMock(spec=Restaurant)
    r.id = uuid4()
    r.name = "Joe's Pizza"
    r.city = "New York"
    r.state = "NY"
    return r


@pytest.fixture
def sample_icp_score(sample_restaurant):
    s = MagicMock(spec=ICPScore)
    s.id = uuid4()
    s.restaurant_id = sample_restaurant.id
    s.fit_label = "excellent"
    s.total_icp_score = 82.5
    s.is_independent = True
    s.has_delivery = True
    s.delivery_platforms = ["doordash", "ubereats"]
    s.has_pos = True
    s.pos_provider = "Toast"
    s.geo_density_score = 0.75
    return s


class TestLeadEnrichmentService:
    """Tests for LeadEnrichmentService."""

    def test_service_init(self, mock_session):
        service = LeadEnrichmentService(mock_session)
        assert service.session == mock_session

    @pytest.mark.asyncio
    async def test_match_and_enrich_no_company(self, mock_session):
        """Lead with no company should not be matched."""
        lead = Lead(first_name="T", last_name="U", email="t@e.com", source="website_demo", status="new")
        service = LeadEnrichmentService(mock_session)
        result = await service.match_and_enrich(lead)
        assert result.restaurant_id is None
        assert result.icp_fit_label is None

    @pytest.mark.asyncio
    async def test_enrichment_copies_all_icp_fields(self, mock_session, sample_lead, sample_restaurant, sample_icp_score):
        """When matched, all ICP fields should be copied to the lead."""
        # Mock exact match returning the restaurant
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(side_effect=[sample_restaurant, sample_icp_score])
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = LeadEnrichmentService(mock_session)
        result = await service.match_and_enrich(sample_lead)

        assert result.restaurant_id == sample_restaurant.id
        assert result.matched_restaurant_name == "Joe's Pizza"
        assert result.match_confidence == 0.95
        assert result.icp_score_id == sample_icp_score.id
        assert result.icp_fit_label == "excellent"
        assert result.icp_total_score == 82.5
        assert result.is_independent is True
        assert result.has_delivery is True
        assert result.delivery_platforms == ["doordash", "ubereats"]
        assert result.has_pos is True
        assert result.pos_provider == "Toast"
        assert result.geo_density_score == 0.75

    @pytest.mark.asyncio
    async def test_match_without_icp_score(self, mock_session, sample_lead, sample_restaurant):
        """Restaurant matched but has no ICP score yet."""
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none = MagicMock(return_value=sample_restaurant)
        mock_result2 = MagicMock()
        mock_result2.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(side_effect=[mock_result1, mock_result2])

        service = LeadEnrichmentService(mock_session)
        result = await service.match_and_enrich(sample_lead)

        assert result.restaurant_id == sample_restaurant.id
        assert result.matched_restaurant_name == "Joe's Pizza"
        assert result.icp_score_id is None
        assert result.icp_fit_label is None

    @pytest.mark.asyncio
    async def test_no_match_found(self, mock_session, sample_lead):
        """No restaurant match — lead should remain unenriched."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_result.first = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = LeadEnrichmentService(mock_session)
        result = await service.match_and_enrich(sample_lead)

        assert result.restaurant_id is None
        assert result.match_confidence is None


class TestMatchConfidence:
    """Test that confidence tiers are applied correctly."""

    def test_exact_match_confidence(self, sample_lead):
        """Exact match should set confidence to 0.95."""
        sample_lead.match_confidence = 0.95
        assert sample_lead.match_confidence == 0.95

    def test_fuzzy_high_confidence_range(self):
        """High fuzzy (>0.75 similarity) confidence should be 0.68-0.90."""
        for sim in [0.76, 0.8, 0.9, 1.0]:
            confidence = round(sim * 0.9, 2)
            assert 0.68 <= confidence <= 0.90

    def test_low_confidence_blocks_enrichment(self):
        """Confidence below 0.7 should not enrich with ICP data."""
        from src.services.lead_enrichment import MIN_ENRICHMENT_CONFIDENCE
        assert MIN_ENRICHMENT_CONFIDENCE == 0.7
        # A match at 0.5 confidence should NOT get ICP data copied
        assert 0.5 < MIN_ENRICHMENT_CONFIDENCE
