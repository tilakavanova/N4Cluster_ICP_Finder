"""Test configuration and fixtures."""

import asyncio
import os

import pytest
import pytest_asyncio

# Prevent real DB/Redis connections during tests
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Async DB session fixture ─────────────────────────────────────────────
#
# Tests that exercise real SQLAlchemy ORM code (services, etc.) need an
# AsyncSession bound to an actual database. We use in-memory SQLite via
# aiosqlite. The models use Postgres-specific column types (JSONB, UUID,
# ARRAY) — we register dialect compilers so SQLAlchemy emits SQLite-
# compatible DDL for those types during table creation in tests.


def _register_sqlite_compat_types():
    """Make Postgres-specific column types compile and bind under SQLite."""
    import json

    from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ARG001
        return "JSON"

    @compiles(UUID, "sqlite")
    def _compile_uuid_sqlite(type_, compiler, **kw):  # noqa: ARG001
        return "CHAR(36)"

    @compiles(ARRAY, "sqlite")
    def _compile_array_sqlite(type_, compiler, **kw):  # noqa: ARG001
        return "JSON"

    # ARRAY columns store Python lists; SQLite (via aiosqlite) can't bind lists
    # natively. Override bind_processor/result_processor on the ARRAY class so
    # values are JSON-serialized when going to SQLite and parsed coming back.
    _orig_array_bind = ARRAY.bind_processor
    _orig_array_result = ARRAY.result_processor

    def _array_bind_processor(self, dialect):
        if dialect.name == "sqlite":
            def process(value):
                if value is None:
                    return None
                return json.dumps(list(value))
            return process
        return _orig_array_bind(self, dialect)

    def _array_result_processor(self, dialect, coltype):
        if dialect.name == "sqlite":
            def process(value):
                if value is None:
                    return None
                if isinstance(value, (list, tuple)):
                    return list(value)
                return json.loads(value)
            return process
        return _orig_array_result(self, dialect, coltype)

    ARRAY.bind_processor = _array_bind_processor
    ARRAY.result_processor = _array_result_processor


_register_sqlite_compat_types()


@pytest_asyncio.fixture
async def db_session():
    """Yield a fresh in-memory SQLite AsyncSession with all tables created."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from src.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()

    await engine.dispose()


# ── ORM-backed fixtures (require db_session) ─────────────────────────────
#
# Tests that exercise real SQLAlchemy ORM code request these fixtures, which
# yield persisted rows in the in-memory SQLite DB.


@pytest_asyncio.fixture
async def sample_restaurant(db_session):
    from src.db.models import Restaurant

    restaurant = Restaurant(
        name="Joe's Pizza",
        address="123 Main St",
        city="New York",
        state="NY",
        zip_code="10001",
    )
    db_session.add(restaurant)
    await db_session.flush()
    return restaurant


@pytest_asyncio.fixture
async def sample_restaurant_with_icp_score(db_session):
    """A Restaurant with an associated ICPScore, suitable for qualification."""
    from src.db.models import ICPScore, Restaurant

    rest = Restaurant(
        name="Test Pizza Co",
        address="123 Test St",
        city="Boston",
        state="MA",
        zip_code="02115",
        is_chain=False,
        review_count=250,
        rating_avg=4.5,
    )
    db_session.add(rest)
    await db_session.flush()

    icp = ICPScore(
        restaurant_id=rest.id,
        total_icp_score=85.0,
        is_independent=True,
        has_delivery=True,
        delivery_platform_count=2,
        delivery_platforms=["doordash", "ubereats"],
        review_volume=250,
        fit_label="excellent",
    )
    db_session.add(icp)
    await db_session.flush()
    return rest


@pytest_asyncio.fixture
async def sample_lead(db_session, sample_restaurant):
    from src.db.models import Lead

    lead = Lead(
        # first_name/last_name/email are NULLable in DB after migration 028,
        # but the ORM model still declares them NOT NULL, so create_all on
        # SQLite emits NOT NULL — supply minimal stub values to satisfy it.
        first_name="Test",
        last_name="Lead",
        email="test.lead@example.com",
        source="prospect_promotion",
        status="new",
        lifecycle_stage="prospect",
        restaurant_id=sample_restaurant.id,
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
def sample_source_records():
    return [
        {
            "source": "google_maps",
            "source_url": "https://maps.google.com/...",
            "raw_data": {"name": "Joe's Pizza", "rating": 4.5},
            "extracted_data": {"cuisine_type": ["Pizza"]},
        },
        {
            "source": "doordash",
            "source_url": "https://doordash.com/store/...",
            "raw_data": {"name": "Joe's Pizza"},
            "extracted_data": None,
            "has_delivery": True,
            "delivery_platform": "doordash",
        },
    ]


@pytest.fixture
def sample_chain_restaurant():
    return {
        "id": "test-uuid-5678",
        "name": "McDonald's",
        "address": "456 Broadway",
        "city": "New York",
        "state": "NY",
        "zip_code": "10002",
        "lat": 40.7200,
        "lng": -73.9980,
        "review_count": 500,
        "rating": 3.5,
    }


@pytest.fixture
def sample_google_places_response():
    return {
        "places": [
            {
                "id": "ChIJ123",
                "displayName": {"text": "Test Restaurant"},
                "formattedAddress": "123 Main St, New York, NY 10001, USA",
                "rating": 4.5,
                "userRatingCount": 200,
                "nationalPhoneNumber": "(555) 123-4567",
                "websiteUri": "https://test.example.com",
                "location": {"latitude": 40.7128, "longitude": -74.0060},
                "primaryType": "italian_restaurant",
                "types": ["italian_restaurant", "restaurant"],
            }
        ]
    }


@pytest.fixture
def sample_yelp_response():
    return {
        "businesses": [
            {
                "id": "yelp-biz-123",
                "name": "Test Restaurant",
                "location": {
                    "address1": "123 Main St",
                    "city": "New York",
                    "state": "NY",
                    "zip_code": "10001",
                },
                "coordinates": {"latitude": 40.7128, "longitude": -74.0060},
                "display_phone": "(555) 123-4567",
                "phone": "+15551234567",
                "rating": 4.5,
                "review_count": 200,
                "categories": [{"alias": "italian", "title": "Italian"}],
                "price": "$$",
                "is_closed": False,
                "url": "https://www.yelp.com/biz/test",
                "transactions": ["delivery", "pickup"],
            }
        ],
        "total": 1,
    }


@pytest.fixture
def multiple_restaurants_for_density():
    return [
        {"id": f"r{i}", "name": f"Restaurant {i}", "lat": 40.71 + i * 0.001, "lng": -74.00 + i * 0.001}
        for i in range(10)
    ]


# ── Celery configuration for tests ───────────────────────────────────────
#
# Run Celery tasks synchronously in the test process so we can call
# `.apply()` / `.delay()` and observe results without a running broker.


@pytest.fixture(autouse=True)
def celery_eager(monkeypatch):
    """Run Celery tasks synchronously in tests."""
    from src.tasks.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)
