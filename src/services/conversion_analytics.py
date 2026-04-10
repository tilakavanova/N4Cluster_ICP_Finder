"""Conversion Intelligence & Neighborhood Penetration Analytics (NIF-148, NIF-149, NIF-150)."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func, and_, extract
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ConversionEvent, ConversionFunnel, Restaurant
from src.utils.logging import get_logger

logger = get_logger("conversion_analytics")

VALID_EVENT_TYPES = {"discovered", "contacted", "demo_scheduled", "pilot_started", "converted", "churned"}


async def record_event(
    session: AsyncSession,
    restaurant_id: UUID,
    event_type: str,
    source: str | None = None,
    lead_id: UUID | None = None,
    metadata: dict | None = None,
) -> ConversionEvent:
    """Record a conversion funnel event (NIF-148)."""
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"Invalid event_type '{event_type}'. Must be one of: {', '.join(sorted(VALID_EVENT_TYPES))}")

    event = ConversionEvent(
        restaurant_id=restaurant_id,
        lead_id=lead_id,
        event_type=event_type,
        source=source,
        metadata_=metadata or {},
    )
    session.add(event)
    await session.flush()

    logger.info("conversion_event_recorded", event_type=event_type, restaurant=str(restaurant_id))
    return event


async def get_funnel(
    session: AsyncSession,
    period: str,
    zip_code: str | None = None,
) -> ConversionFunnel | None:
    """Get existing funnel summary for a period (NIF-149)."""
    query = select(ConversionFunnel).where(ConversionFunnel.period == period)
    if zip_code:
        query = query.where(ConversionFunnel.zip_code == zip_code)
    else:
        query = query.where(ConversionFunnel.zip_code.is_(None))
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def calculate_funnel(
    session: AsyncSession,
    period: str,
    zip_code: str | None = None,
) -> ConversionFunnel:
    """Aggregate conversion events into a funnel summary (NIF-149)."""
    # Build base filter: events whose occurred_at matches the period string
    # Period can be "2026-W15" (ISO week) or "2026-04" (month)
    base_filter = []

    if "-W" in period:
        # ISO week format: "2026-W15"
        parts = period.split("-W")
        year = int(parts[0])
        week = int(parts[1])
        base_filter.append(extract("isoyear", ConversionEvent.occurred_at) == year)
        base_filter.append(extract("week", ConversionEvent.occurred_at) == week)
    else:
        # Month format: "2026-04"
        parts = period.split("-")
        year = int(parts[0])
        month = int(parts[1])
        base_filter.append(extract("year", ConversionEvent.occurred_at) == year)
        base_filter.append(extract("month", ConversionEvent.occurred_at) == month)

    if zip_code:
        base_filter.append(
            ConversionEvent.restaurant_id.in_(
                select(Restaurant.id).where(Restaurant.zip_code == zip_code)
            )
        )

    # Count each event type
    counts = {}
    for event_type in VALID_EVENT_TYPES:
        count_query = select(func.count(ConversionEvent.id)).where(
            and_(*base_filter, ConversionEvent.event_type == event_type)
        )
        result = await session.execute(count_query)
        counts[event_type] = result.scalar() or 0

    # Compute conversion rate (discovered -> converted)
    discovered = counts.get("discovered", 0)
    converted = counts.get("converted", 0)
    conversion_rate = round((converted / discovered) * 100.0, 2) if discovered > 0 else 0.0

    # Compute average days to convert
    # For restaurants that have both "discovered" and "converted" events in this period
    avg_days_query = select(
        func.avg(
            extract("epoch", func.max(ConversionEvent.occurred_at))
            - extract("epoch", func.min(ConversionEvent.occurred_at))
        ) / 86400.0
    ).where(
        and_(
            *base_filter,
            ConversionEvent.event_type.in_(["discovered", "converted"]),
        )
    ).group_by(ConversionEvent.restaurant_id).having(
        func.count(func.distinct(ConversionEvent.event_type)) == 2
    )
    avg_result = await session.execute(select(func.avg(avg_days_query.subquery().c[0])))
    avg_days = avg_result.scalar() or 0.0

    # Upsert funnel record
    funnel = await get_funnel(session, period, zip_code)
    now = datetime.now(timezone.utc)

    if not funnel:
        funnel = ConversionFunnel(period=period, zip_code=zip_code)
        session.add(funnel)

    funnel.discovered = counts["discovered"]
    funnel.contacted = counts["contacted"]
    funnel.demo_scheduled = counts["demo_scheduled"]
    funnel.pilot_started = counts["pilot_started"]
    funnel.converted = counts["converted"]
    funnel.churned = counts["churned"]
    funnel.conversion_rate = conversion_rate
    funnel.avg_days_to_convert = round(float(avg_days), 2)
    funnel.last_calculated_at = now

    await session.flush()
    logger.info("funnel_calculated", period=period, zip_code=zip_code, conversion_rate=conversion_rate)
    return funnel


async def get_conversion_timeline(
    session: AsyncSession,
    restaurant_id: UUID,
) -> list[ConversionEvent]:
    """Get all conversion events for a restaurant, ordered chronologically (NIF-150)."""
    query = (
        select(ConversionEvent)
        .where(ConversionEvent.restaurant_id == restaurant_id)
        .order_by(ConversionEvent.occurred_at.asc())
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_funnel_trends(
    session: AsyncSession,
    periods: list[str],
    zip_code: str | None = None,
) -> list[ConversionFunnel]:
    """Get funnel summaries across multiple periods for trend analysis (NIF-150)."""
    query = select(ConversionFunnel).where(ConversionFunnel.period.in_(periods))
    if zip_code:
        query = query.where(ConversionFunnel.zip_code == zip_code)
    else:
        query = query.where(ConversionFunnel.zip_code.is_(None))
    query = query.order_by(ConversionFunnel.period.asc())
    result = await session.execute(query)
    return list(result.scalars().all())
