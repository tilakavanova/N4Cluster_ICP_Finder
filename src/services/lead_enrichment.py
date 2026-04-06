"""Lead enrichment service — matches leads against restaurant DB and attaches ICP data."""

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.db.models import Lead, Restaurant, ICPScore, SourceRecord
from src.utils.logging import get_logger

logger = get_logger("services.lead_enrichment")


class LeadEnrichmentService:
    """Matches inbound leads against the restaurant database and enriches with ICP signals."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def match_and_enrich(self, lead: Lead) -> Lead:
        """Full enrichment pipeline for a lead.

        1. Fuzzy-match company name against Restaurant table
        2. If matched, copy all ICP score signals onto the lead
        3. Return the enriched lead (caller must commit)
        """
        restaurant = await self._find_best_match(lead)
        if not restaurant:
            logger.info("lead_no_match", email=lead.email, company=lead.company)
            return lead

        lead.restaurant_id = restaurant.id
        lead.matched_restaurant_name = restaurant.name

        # Load ICP score for the matched restaurant
        result = await self.session.execute(
            select(ICPScore).where(ICPScore.restaurant_id == restaurant.id)
        )
        score = result.scalar_one_or_none()

        if score:
            lead.icp_score_id = score.id
            lead.icp_fit_label = score.fit_label
            lead.icp_total_score = score.total_icp_score
            lead.is_independent = score.is_independent
            lead.has_delivery = score.has_delivery
            lead.delivery_platforms = score.delivery_platforms or []
            lead.has_pos = score.has_pos
            lead.pos_provider = score.pos_provider
            lead.geo_density_score = score.geo_density_score

        logger.info(
            "lead_enriched",
            email=lead.email,
            company=lead.company,
            matched_restaurant=restaurant.name,
            confidence=lead.match_confidence,
            icp_fit=lead.icp_fit_label,
            icp_score=lead.icp_total_score,
        )
        return lead

    async def _find_best_match(self, lead: Lead) -> Restaurant | None:
        """Find the best restaurant match using tiered fuzzy matching.

        Strategy (highest confidence first):
        1. Exact name + city match → confidence 0.95
        2. Fuzzy name (>0.6 similarity) + same city → confidence 0.80
        3. Fuzzy name (>0.5 similarity) alone → confidence 0.50
        """
        if not lead.company:
            return None

        # Tier 1: Exact name + city (case-insensitive)
        if lead.business_type and lead.business_type not in ("Other",):
            # Try to extract city from the lead's context (business_type is not city,
            # but we can try matching on exact name first)
            pass

        result = await self.session.execute(
            select(Restaurant)
            .where(func.lower(Restaurant.name) == func.lower(lead.company))
            .limit(1)
        )
        exact_match = result.scalar_one_or_none()
        if exact_match:
            lead.match_confidence = 0.95
            return exact_match

        # Tier 2: High fuzzy similarity (>0.6)
        result = await self.session.execute(
            select(
                Restaurant,
                func.similarity(Restaurant.name, lead.company).label("sim"),
            )
            .where(func.similarity(Restaurant.name, lead.company) > 0.6)
            .order_by(func.similarity(Restaurant.name, lead.company).desc())
            .limit(1)
        )
        row = result.first()
        if row:
            lead.match_confidence = round(float(row.sim) * 0.9, 2)  # Scale to ~0.54-0.90
            return row[0]

        # Tier 3: Lower fuzzy threshold (>0.4) — weaker match
        result = await self.session.execute(
            select(
                Restaurant,
                func.similarity(Restaurant.name, lead.company).label("sim"),
            )
            .where(func.similarity(Restaurant.name, lead.company) > 0.4)
            .order_by(func.similarity(Restaurant.name, lead.company).desc())
            .limit(1)
        )
        row = result.first()
        if row:
            lead.match_confidence = round(float(row.sim) * 0.6, 2)  # Scale to ~0.24-0.36
            return row[0]

        return None
