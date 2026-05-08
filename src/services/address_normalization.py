"""Address normalization via Google Geocoding API (NIF-263)."""

from uuid import UUID

import httpx
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import Restaurant
from src.utils.logging import get_logger

logger = get_logger("address_normalization")

GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"


async def normalize_address(
    address: str,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
) -> dict:
    """Call Google Geocoding API to get standardized address components.

    Returns a dict with normalized fields or an error.
    """
    api_key = settings.effective_geocoding_key
    if not api_key:
        return {"error": "Google Geocoding API key not configured"}

    # Build the full address string
    parts = [address]
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    if zip_code:
        parts.append(zip_code)
    full_address = ", ".join(p for p in parts if p)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            GEOCODING_URL,
            params={"address": full_address, "key": api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") != "OK" or not data.get("results"):
        return {
            "error": f"Geocoding failed: {data.get('status', 'UNKNOWN')}",
            "status": data.get("status"),
        }

    result = data["results"][0]
    components = result.get("address_components", [])
    location = result.get("geometry", {}).get("location", {})

    # Extract standardized components
    normalized: dict = {
        "formatted_address": result.get("formatted_address"),
        "lat": location.get("lat"),
        "lng": location.get("lng"),
        "street_number": None,
        "street_name": None,
        "city": None,
        "state": None,
        "zip_code": None,
        "country": None,
        "place_id": result.get("place_id"),
    }

    for comp in components:
        types = comp.get("types", [])
        if "street_number" in types:
            normalized["street_number"] = comp.get("long_name")
        elif "route" in types:
            normalized["street_name"] = comp.get("long_name")
        elif "locality" in types:
            normalized["city"] = comp.get("long_name")
        elif "administrative_area_level_1" in types:
            normalized["state"] = comp.get("short_name")
        elif "postal_code" in types:
            normalized["zip_code"] = comp.get("long_name")
        elif "country" in types:
            normalized["country"] = comp.get("short_name")

    # Build normalized street address
    if normalized["street_number"] and normalized["street_name"]:
        normalized["address"] = f"{normalized['street_number']} {normalized['street_name']}"
    elif normalized["street_name"]:
        normalized["address"] = normalized["street_name"]
    else:
        normalized["address"] = address  # fallback to original

    logger.info(
        "address_normalized",
        original=full_address,
        formatted=normalized["formatted_address"],
    )
    return normalized


async def geocode_restaurant(
    session: AsyncSession,
    restaurant_id: UUID,
) -> dict:
    """Normalize address and update lat/lng for a specific restaurant."""
    result = await session.execute(
        select(Restaurant).where(Restaurant.id == restaurant_id)
    )
    restaurant = result.scalar_one_or_none()
    if not restaurant:
        return {"error": f"Restaurant {restaurant_id} not found"}

    if not restaurant.address:
        return {"error": "Restaurant has no address to normalize", "restaurant_id": str(restaurant_id)}

    normalized = await normalize_address(
        address=restaurant.address,
        city=restaurant.city,
        state=restaurant.state,
        zip_code=restaurant.zip_code,
    )

    if "error" in normalized:
        return normalized

    # Update restaurant fields
    updated_fields = []
    if normalized.get("address") and normalized["address"] != restaurant.address:
        restaurant.address = normalized["address"]
        updated_fields.append("address")
    if normalized.get("city") and normalized["city"] != restaurant.city:
        restaurant.city = normalized["city"]
        updated_fields.append("city")
    if normalized.get("state") and normalized["state"] != restaurant.state:
        restaurant.state = normalized["state"]
        updated_fields.append("state")
    if normalized.get("zip_code") and normalized["zip_code"] != restaurant.zip_code:
        restaurant.zip_code = normalized["zip_code"]
        updated_fields.append("zip_code")
    if normalized.get("lat") is not None and (restaurant.lat is None or restaurant.lng is None):
        restaurant.lat = normalized["lat"]
        restaurant.lng = normalized["lng"]
        updated_fields.extend(["lat", "lng"])

    await session.flush()

    logger.info(
        "restaurant_geocoded",
        restaurant_id=str(restaurant_id),
        updated_fields=updated_fields,
    )
    return {
        "restaurant_id": str(restaurant_id),
        "normalized": normalized,
        "updated_fields": updated_fields,
    }


async def batch_normalize(
    session: AsyncSession,
    limit: int = 100,
) -> dict:
    """Process restaurants with missing or inconsistent addresses."""
    # Find restaurants missing lat/lng or with potentially unnormalized addresses
    query = (
        select(Restaurant)
        .where(
            or_(
                Restaurant.lat.is_(None),
                Restaurant.lng.is_(None),
            )
        )
        .where(Restaurant.address.isnot(None))
        .limit(limit)
    )
    result = await session.execute(query)
    restaurants = result.scalars().all()

    processed = 0
    errors = 0
    results = []

    for restaurant in restaurants:
        try:
            res = await geocode_restaurant(session, restaurant.id)
            if "error" in res:
                errors += 1
                results.append({"restaurant_id": str(restaurant.id), "error": res["error"]})
            else:
                processed += 1
                results.append({"restaurant_id": str(restaurant.id), "status": "ok"})
        except Exception as exc:
            errors += 1
            results.append({"restaurant_id": str(restaurant.id), "error": str(exc)})
            logger.error("batch_normalize_error", restaurant_id=str(restaurant.id), error=str(exc))

    logger.info("batch_normalize_complete", processed=processed, errors=errors, total=len(restaurants))
    return {
        "total": len(restaurants),
        "processed": processed,
        "errors": errors,
        "results": results,
    }
