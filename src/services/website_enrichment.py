"""Website enrichment service — crawls restaurant websites for POS detection."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.crawlers.website import WebsiteCrawler
from src.db.models import Restaurant, SourceRecord, ICPScore
from src.scoring.signals import detect_pos, detect_chain
from src.utils.logging import get_logger

logger = get_logger("services.website_enrichment")


class WebsiteEnrichmentService:
    """Crawls restaurant websites and extracts POS + chain signals."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.crawler = WebsiteCrawler()

    async def enrich_batch(self, limit: int = 50) -> dict:
        """Find restaurants with websites but no website source_record, crawl them.

        Returns summary of enrichment results.
        """
        # Find restaurants with website URL but no website source_record
        has_website_sr = (
            select(SourceRecord.restaurant_id)
            .where(SourceRecord.source == "website")
        )
        result = await self.session.execute(
            select(Restaurant)
            .where(
                Restaurant.website.isnot(None),
                Restaurant.website != "",
                Restaurant.id.notin_(has_website_sr),
            )
            .limit(limit)
        )
        restaurants = result.scalars().all()

        if not restaurants:
            logger.info("no_restaurants_to_enrich")
            return {"enriched": 0, "pos_detected": 0, "chains_detected": 0, "errors": 0}

        logger.info("starting_website_enrichment", count=len(restaurants))

        enriched = 0
        pos_detected = 0
        chains_detected = 0
        errors = 0

        for restaurant in restaurants:
            try:
                result = await self._enrich_one(restaurant)
                enriched += 1
                if result.get("has_pos"):
                    pos_detected += 1
                if result.get("is_chain"):
                    chains_detected += 1
            except Exception as e:
                errors += 1
                logger.warning("enrich_failed", restaurant=restaurant.name, error=str(e))

            # Rate limit: 1 request per second
            await asyncio.sleep(1.0)

        await self.session.commit()

        summary = {
            "enriched": enriched,
            "pos_detected": pos_detected,
            "chains_detected": chains_detected,
            "errors": errors,
            "total_candidates": len(restaurants),
        }
        logger.info("website_enrichment_complete", **summary)
        return summary

    async def _enrich_one(self, restaurant: Restaurant) -> dict:
        """Crawl one restaurant website and extract POS + chain signals."""
        url = restaurant.website
        if not url:
            return {}

        # Normalize URL
        if not url.startswith("http"):
            url = "https://" + url

        # Crawl website
        data = await self.crawler.crawl_url(url)
        raw_text = data.get("raw_text", "")

        # Store source record
        sr = SourceRecord(
            restaurant_id=restaurant.id,
            source="website",
            source_url=url,
            raw_data={"raw_text": raw_text[:5000]},  # Truncate for storage
            crawled_at=datetime.now(timezone.utc),
        )
        self.session.add(sr)

        result = {"has_pos": False, "pos_provider": None, "is_chain": False}

        if not raw_text or data.get("error"):
            return result

        # Detect POS from website text
        has_pos, pos_provider = detect_pos(raw_text)
        if has_pos:
            result["has_pos"] = True
            result["pos_provider"] = pos_provider
            logger.info("pos_found", restaurant=restaurant.name, provider=pos_provider)

            # Update ICPScore if exists
            score_result = await self.session.execute(
                select(ICPScore).where(ICPScore.restaurant_id == restaurant.id)
            )
            score = score_result.scalar_one_or_none()
            if score:
                score.has_pos = True
                score.pos_provider = pos_provider

        # Detect chain from website text
        is_chain, chain_name = detect_chain(restaurant.name)
        if not is_chain and raw_text:
            # Check for franchise indicators in website text
            franchise_keywords = ["franchise", "locations nationwide", "join our team at any location"]
            text_lower = raw_text.lower()
            for kw in franchise_keywords:
                if kw in text_lower:
                    is_chain = True
                    break

        if is_chain:
            result["is_chain"] = True
            restaurant.is_chain = True
            logger.info("chain_detected_from_website", restaurant=restaurant.name)

        return result
