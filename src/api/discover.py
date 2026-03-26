"""Real-time restaurant discovery — crawls on-demand when no cached data exists."""

import asyncio
import re
import time
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from src.db.models import Restaurant, SourceRecord
from src.crawlers.google_maps import GoogleMapsCrawler
from src.utils.geo import haversine_miles, bounding_box
from src.utils.logging import get_logger

logger = get_logger("api.discover")

# Max time for an inline crawl (seconds)
CRAWL_TIMEOUT = 25

# ZIP code pattern
ZIP_RE = re.compile(r"^\d{5}$")


def parse_location(location: str) -> dict:
    """Parse location string into structured components.

    Supports:
      - "10001" (ZIP code)
      - "New York, NY"
      - "Issaquah, WA"
      - "Seattle"
    """
    location = location.strip()

    if ZIP_RE.match(location):
        return {"type": "zip", "zip_code": location, "query": f"restaurants near {location}"}

    return {"type": "city", "raw": location, "query": f"restaurants in {location}"}


async def find_cached_restaurants(
    session: AsyncSession,
    location: str,
    radius_miles: float,
    cuisine: str | None,
    limit: int,
) -> tuple[list[dict], str | None]:
    """Check DB for existing restaurants matching the location.

    Returns (results_list, location_type) or ([], None) if no cached data.
    """
    parsed = parse_location(location)

    if parsed["type"] == "zip":
        # Find centroid from ZIP
        centroid_q = select(
            func.avg(Restaurant.lat).label("clat"),
            func.avg(Restaurant.lng).label("clng"),
            func.count(Restaurant.id).label("cnt"),
        ).where(
            Restaurant.zip_code == parsed["zip_code"],
            Restaurant.lat.isnot(None),
            Restaurant.lng.isnot(None),
        )
        row = (await session.execute(centroid_q)).one()
        if not row.clat or row.cnt == 0:
            return [], None
        center_lat, center_lng = float(row.clat), float(row.clng)

    else:
        # Parse "City, ST" or just "City"
        parts = [p.strip() for p in parsed["raw"].split(",")]
        city = parts[0]
        state = parts[1].upper() if len(parts) > 1 else None

        city_q = select(
            func.avg(Restaurant.lat).label("clat"),
            func.avg(Restaurant.lng).label("clng"),
            func.count(Restaurant.id).label("cnt"),
        ).where(
            Restaurant.city.ilike(f"%{city}%"),
            Restaurant.lat.isnot(None),
        )
        if state and len(state) == 2:
            city_q = city_q.where(Restaurant.state == state)

        row = (await session.execute(city_q)).one()
        if not row.clat or row.cnt == 0:
            return [], None
        center_lat, center_lng = float(row.clat), float(row.clng)

    # Bounding box + haversine filter
    min_lat, max_lat, min_lng, max_lng = bounding_box(center_lat, center_lng, radius_miles)
    q = select(Restaurant).where(
        Restaurant.lat.isnot(None),
        Restaurant.lng.isnot(None),
        Restaurant.lat.between(min_lat, max_lat),
        Restaurant.lng.between(min_lng, max_lng),
    )
    if cuisine:
        q = q.where(Restaurant.cuisine_type.any(cuisine))

    candidates = (await session.execute(q)).scalars().all()

    results = []
    for r in candidates:
        dist = haversine_miles(center_lat, center_lng, r.lat, r.lng)
        if dist <= radius_miles:
            results.append({
                "name": r.name,
                "address": r.address,
                "city": r.city,
                "state": r.state,
                "zip_code": r.zip_code,
                "lat": r.lat,
                "lng": r.lng,
                "phone": r.phone,
                "website": r.website,
                "cuisine": ", ".join(r.cuisine_type or []),
                "rating": None,
                "review_count": None,
                "distance_miles": round(dist, 2),
                "source": "google_maps",
            })

    results.sort(key=lambda x: x["distance_miles"])
    return results[:limit], "cached"


async def crawl_and_persist(
    session: AsyncSession,
    location: str,
    limit: int,
) -> list[dict]:
    """Run an inline Google Places crawl and persist results to DB.

    Returns list of restaurant dicts.
    """
    parsed = parse_location(location)
    crawler = GoogleMapsCrawler()

    logger.info("inline_crawl_starting", location=location)

    try:
        results = await asyncio.wait_for(
            crawler.run("restaurants", parsed.get("raw", location)),
            timeout=CRAWL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("inline_crawl_timeout", location=location)
        results = []
    except Exception as e:
        logger.error("inline_crawl_failed", location=location, error=str(e))
        results = []

    if not results:
        return []

    logger.info("inline_crawl_results", location=location, count=len(results))

    # Persist to DB
    persisted = []
    for record in results:
        name = record.get("name", "").strip()
        address = record.get("address", "").strip()
        if not name:
            continue

        cuisine_raw = record.get("cuisine", "")
        cuisine_list = [cuisine_raw] if cuisine_raw and cuisine_raw != "Restaurant" else []

        try:
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
                cuisine_type=cuisine_list,
            ).on_conflict_do_update(
                constraint="uq_restaurant_name_address",
                set_={
                    "lat": record.get("lat"),
                    "lng": record.get("lng"),
                    "phone": record.get("phone"),
                    "website": record.get("website"),
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await session.execute(stmt)
            await session.flush()

            # Fetch persisted restaurant for source record
            rest = (await session.execute(
                select(Restaurant).where(
                    Restaurant.name == name,
                    Restaurant.address == (address or None),
                )
            )).scalar_one_or_none()

            if rest:
                sr = SourceRecord(
                    restaurant_id=rest.id,
                    source="google_maps",
                    source_url=record.get("source_url"),
                    raw_data=record,
                    crawled_at=datetime.now(timezone.utc),
                )
                session.add(sr)

            persisted.append({
                "name": name,
                "address": address,
                "city": record.get("city"),
                "state": record.get("state"),
                "zip_code": record.get("zip_code"),
                "lat": record.get("lat"),
                "lng": record.get("lng"),
                "phone": record.get("phone"),
                "website": record.get("website"),
                "cuisine": cuisine_raw,
                "rating": record.get("rating"),
                "review_count": record.get("review_count"),
                "distance_miles": None,
                "source": "google_maps",
            })
        except Exception as e:
            logger.warning("persist_error", name=name, error=str(e))
            continue

    await session.commit()
    logger.info("inline_crawl_persisted", location=location, count=len(persisted))
    return persisted[:limit]
