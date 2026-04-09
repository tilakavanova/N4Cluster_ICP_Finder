"""Celery tasks for crawling operations."""

import asyncio
from datetime import datetime, timezone

from src.tasks.celery_app import celery_app
from src.utils.logging import get_logger

logger = get_logger("tasks.crawl")


def run_async(coro):
    """Helper to run async code in sync Celery tasks."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, name="src.tasks.crawl_tasks.crawl_source")
def crawl_source(self, source: str, query: str, location: str, job_id: str | None = None):
    """Crawl a single source for restaurants."""
    logger.info("crawl_task_started", source=source, query=query, location=location, job_id=job_id)

    async def _crawl():
        from src.db.session import async_session
        from src.db.models import CrawlJob, SourceRecord, Restaurant, RestaurantChange
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert

        crawler = _get_crawler(source)
        if not crawler:
            raise ValueError(f"Unknown source: {source}")

        async with async_session() as session:
            # Update job status
            if job_id:
                job = await session.get(CrawlJob, job_id)
                if job:
                    job.status = "running"
                    job.started_at = datetime.now(timezone.utc)
                    await session.commit()

            try:
                results = await crawler.run(query, location)
                logger.info("crawl_results_received", source=source, total_from_api=len(results))
                count = 0
                skipped = 0

                for record in results:
                    name = record.get("name", "").strip()
                    address = record.get("address", "").strip()
                    if not name:
                        skipped += 1
                        continue

                    cuisine = record.get("cuisine_type") or []
                    if not isinstance(cuisine, list):
                        cuisine = [cuisine] if cuisine else []
                    if not cuisine and record.get("cuisine"):
                        c = record["cuisine"]
                        cuisine = [c] if c and c != "Restaurant" else []

                    rating = record.get("rating")
                    review_count = record.get("review_count", 0) or 0
                    price_tier = record.get("price_tier")

                    # Check for existing restaurant to detect changes
                    existing_query = select(Restaurant).where(
                        Restaurant.name == name,
                        Restaurant.address == (address or None),
                    )
                    existing = (await session.execute(existing_query)).scalar_one_or_none()

                    stmt = insert(Restaurant).values(
                        name=name,
                        address=address or None,
                        city=record.get("city"),
                        state=record.get("state"),
                        zip_code=record.get("zip_code"),
                        lat=record.get("lat"),
                        lng=record.get("lng"),
                        phone=record.get("phone"),
                        website=record.get("website"),
                        cuisine_type=cuisine,
                        rating_avg=rating,
                        review_count=review_count,
                        price_tier=price_tier,
                    ).on_conflict_do_update(
                        constraint="uq_restaurant_name_address",
                        set_={
                            "lat": record.get("lat"),
                            "lng": record.get("lng"),
                            "phone": record.get("phone"),
                            "website": record.get("website"),
                            "cuisine_type": cuisine,
                            "rating_avg": rating,
                            "review_count": review_count,
                            "price_tier": price_tier,
                            "updated_at": datetime.now(timezone.utc),
                        },
                    )
                    await session.execute(stmt)
                    await session.flush()

                    # Get the restaurant ID
                    rest_query = select(Restaurant).where(
                        Restaurant.name == name,
                        Restaurant.address == (address or None),
                    )
                    rest = (await session.execute(rest_query)).scalar_one_or_none()

                    # --- Change Detection ---
                    if rest and not existing:
                        # New restaurant detected
                        session.add(RestaurantChange(
                            restaurant_id=rest.id,
                            change_type="new_restaurant",
                            source=record.get("source", source),
                        ))
                    elif rest and existing:
                        src_name = record.get("source", source)
                        # Rating change
                        if rating is not None and existing.rating_avg is not None and abs((rating or 0) - (existing.rating_avg or 0)) >= 0.1:
                            session.add(RestaurantChange(
                                restaurant_id=rest.id,
                                change_type="rating_change",
                                field_name="rating_avg",
                                old_value=str(existing.rating_avg),
                                new_value=str(rating),
                                source=src_name,
                            ))
                        # Review count change
                        if review_count and existing.review_count and review_count != existing.review_count:
                            session.add(RestaurantChange(
                                restaurant_id=rest.id,
                                change_type="field_update",
                                field_name="review_count",
                                old_value=str(existing.review_count),
                                new_value=str(review_count),
                                source=src_name,
                            ))
                        # Delivery/platform change (detected from source type)
                        raw = record.get("raw_data", record) or record
                        new_has_delivery = raw.get("has_delivery", False)
                        new_platforms = raw.get("delivery_platforms") or raw.get("delivery_platform")
                        if record.get("source", source) in ("doordash", "ubereats", "grubhub", "delivery"):
                            session.add(RestaurantChange(
                                restaurant_id=rest.id,
                                change_type="delivery_change",
                                field_name="delivery_source",
                                new_value=record.get("source", source),
                                source=src_name,
                            ))
                        # Price tier change
                        if price_tier and existing.price_tier and price_tier != existing.price_tier:
                            session.add(RestaurantChange(
                                restaurant_id=rest.id,
                                change_type="field_update",
                                field_name="price_tier",
                                old_value=existing.price_tier,
                                new_value=price_tier,
                                source=src_name,
                            ))

                    if rest:
                        source_rec = SourceRecord(
                            restaurant_id=rest.id,
                            source=record.get("source", source),
                            source_url=record.get("source_url"),
                            raw_data=record,
                            crawled_at=datetime.now(timezone.utc),
                        )
                        session.add(source_rec)
                        count += 1
                    else:
                        logger.warning("restaurant_not_found_after_insert", name=name, address=address[:80] if address else None)

                logger.info("crawl_insert_summary", source=source, inserted=count, skipped=skipped, total_from_api=len(results))
                await session.commit()

                if job_id:
                    job = await session.get(CrawlJob, job_id)
                    if job:
                        job.status = "done"
                        job.total_items = count
                        job.finished_at = datetime.now(timezone.utc)
                        await session.commit()

                logger.info("crawl_task_complete", source=source, items=count)
                return {"source": source, "items_crawled": count, "job_id": job_id}

            except Exception as e:
                if job_id:
                    async with async_session() as err_session:
                        job = await err_session.get(CrawlJob, job_id)
                        if job:
                            job.status = "failed"
                            job.error_message = str(e)
                            job.finished_at = datetime.now(timezone.utc)
                            await err_session.commit()
                raise

    return run_async(_crawl())


@celery_app.task(name="src.tasks.crawl_tasks.run_daily_crawl")
def run_daily_crawl():
    """Scheduled daily crawl for configured locations."""
    logger.info("daily_crawl_triggered")
    locations = [
        ("restaurants", "New York, NY"),
        ("restaurants", "Los Angeles, CA"),
        ("restaurants", "Chicago, IL"),
    ]
    sources = ["google_maps", "yelp", "delivery"]

    from celery import group
    tasks = []
    for query, location in locations:
        for source in sources:
            tasks.append(crawl_source.s(source, query, location))

    job = group(tasks)
    job.apply_async()
    logger.info("daily_crawl_dispatched", task_count=len(tasks))


def _get_crawler(source: str):
    """Factory to get crawler instance by source name."""
    from src.crawlers.google_maps import GoogleMapsCrawler
    from src.crawlers.yelp import YelpCrawler
    from src.crawlers.delivery import DeliveryCrawler
    from src.crawlers.website import WebsiteCrawler

    crawlers = {
        "google_maps": GoogleMapsCrawler,
        "yelp": YelpCrawler,
        "delivery": DeliveryCrawler,
        "doordash": DeliveryCrawler,
        "ubereats": DeliveryCrawler,
        "website": WebsiteCrawler,
    }
    cls = crawlers.get(source)
    return cls() if cls else None
