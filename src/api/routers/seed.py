"""Data seeding and import endpoints."""

import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session
from src.db.models import Restaurant, SourceRecord, ICPScore
from src.scoring.icp_scorer import icp_scorer
from src.scoring.signals import detect_chain
from src.utils.logging import get_logger

logger = get_logger("api.seed")

router = APIRouter(prefix="/seed", tags=["seed"])

SAMPLE_DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "sample_restaurants.json"


@router.post("/sample")
async def seed_sample_data(session: AsyncSession = Depends(get_session)):
    """Load the bundled sample restaurant dataset and score all entries."""
    if not SAMPLE_DATA_PATH.exists():
        return {"error": "Sample data file not found", "path": str(SAMPLE_DATA_PATH)}

    data = json.loads(SAMPLE_DATA_PATH.read_text())
    return await _import_and_score(data, session)


@router.post("/import")
async def import_json(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Import restaurants from an uploaded JSON file and score them."""
    content = await file.read()
    data = json.loads(content)
    if isinstance(data, dict):
        data = [data]
    return await _import_and_score(data, session)


@router.post("/manual")
async def add_restaurant(
    payload: dict,
    session: AsyncSession = Depends(get_session),
):
    """Manually add a single restaurant and score it."""
    return await _import_and_score([payload], session)


async def _import_and_score(data: list[dict], session: AsyncSession) -> dict:
    """Import restaurants, create source records, and compute ICP scores."""
    imported = 0
    scored = 0

    for entry in data:
        name = entry.get("name", "").strip()
        if not name:
            continue

        address = entry.get("address", "").strip() or None
        cuisine = entry.get("cuisine_type", [])
        if isinstance(cuisine, str):
            cuisine = [c.strip() for c in cuisine.split(",")]

        # Upsert restaurant
        stmt = insert(Restaurant).values(
            name=name,
            address=address,
            city=entry.get("city"),
            state=entry.get("state"),
            zip_code=entry.get("zip_code"),
            lat=entry.get("lat"),
            lng=entry.get("lng"),
            phone=entry.get("phone"),
            website=entry.get("website"),
            cuisine_type=cuisine,
            is_chain=entry.get("is_chain", False),
            chain_name=entry.get("chain_name"),
        ).on_conflict_do_update(
            constraint="uq_restaurant_name_address",
            set_={
                "city": entry.get("city"),
                "state": entry.get("state"),
                "lat": entry.get("lat"),
                "lng": entry.get("lng"),
                "phone": entry.get("phone"),
                "website": entry.get("website"),
                "cuisine_type": cuisine,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await session.execute(stmt)

        # Fetch the restaurant
        rest = (await session.execute(
            select(Restaurant).where(Restaurant.name == name, Restaurant.address == address)
        )).scalar_one_or_none()

        if not rest:
            continue
        imported += 1

        # Create source record
        source_rec = SourceRecord(
            restaurant_id=rest.id,
            source="manual_import",
            raw_data=entry,
            extracted_data=entry,
            crawled_at=datetime.now(timezone.utc),
        )
        session.add(source_rec)

        # Build source records for scoring
        source_records = []
        if entry.get("has_delivery"):
            for platform in entry.get("delivery_platforms", ["unknown"]):
                source_records.append({
                    "source": platform,
                    "has_delivery": True,
                    "delivery_platform": platform,
                    "extracted_data": entry,
                })
        if entry.get("has_pos"):
            source_records.append({
                "source": "website",
                "raw_data": {"raw_text": f"Powered by {entry.get('pos_provider', 'POS')}"},
                "extracted_data": {
                    "has_pos": True,
                    "pos_provider": entry.get("pos_provider"),
                },
            })
        if not source_records:
            source_records.append({"source": "manual_import", "extracted_data": entry})

        # Score
        restaurant_dict = {
            "name": name,
            "address": address,
            "review_count": entry.get("review_count", 0),
            "rating": entry.get("rating", 0.0),
            "extracted_data": entry,
        }

        density = 0.5  # Default density for imported data
        score = icp_scorer.score_restaurant(restaurant_dict, source_records, density_score=density)

        # Upsert ICP score
        score_stmt = insert(ICPScore).values(
            restaurant_id=rest.id,
            is_independent=score["is_independent"],
            has_delivery=score["has_delivery"],
            delivery_platforms=score["delivery_platforms"],
            has_pos=score["has_pos"],
            pos_provider=score["pos_provider"],
            geo_density_score=score["geo_density_score"],
            review_volume=score["review_volume"],
            rating_avg=score["rating_avg"],
            total_icp_score=score["total_icp_score"],
            fit_label=score["fit_label"],
            scoring_version=score["scoring_version"],
            scored_at=datetime.now(timezone.utc),
        ).on_conflict_do_update(
            index_elements=["restaurant_id"],
            set_={
                "is_independent": score["is_independent"],
                "has_delivery": score["has_delivery"],
                "delivery_platforms": score["delivery_platforms"],
                "has_pos": score["has_pos"],
                "pos_provider": score["pos_provider"],
                "geo_density_score": score["geo_density_score"],
                "review_volume": score["review_volume"],
                "rating_avg": score["rating_avg"],
                "total_icp_score": score["total_icp_score"],
                "fit_label": score["fit_label"],
                "scoring_version": score["scoring_version"],
                "scored_at": datetime.now(timezone.utc),
            },
        )
        await session.execute(score_stmt)
        scored += 1

    await session.commit()

    logger.info("import_complete", imported=imported, scored=scored)
    return {
        "message": f"Imported {imported} restaurants, scored {scored}",
        "imported": imported,
        "scored": scored,
    }
