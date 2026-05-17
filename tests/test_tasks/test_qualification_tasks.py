"""Tests for NIF-284: qualify_restaurant_task Celery task.

The task wraps the existing `qualify_restaurant` service for async invocation
from the prospect-to-outreach promotion orchestrator. These tests run the task
in Celery eager mode (configured by the `celery_eager` autouse fixture).

The task creates its own AsyncSession via `src.db.session.async_session` and
runs the coroutine via `asyncio.new_event_loop().run_until_complete(...)`.
That means we cannot drive the task from inside an existing running loop
(pytest-asyncio tests). Instead these tests are SYNC: they build a shared
SQLite engine with a single static connection (so successive event loops
can reuse the same in-memory DB), monkeypatch the task module to use a
sessionmaker bound to that engine, and use a small `_run` helper to drive
async setup / verification in fresh loops between Celery invocations.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.db.models import Base
from src.tasks.qualification_tasks import qualify_restaurant_task


def _run(coro):
    """Run an async coroutine in a fresh event loop (matches the task's pattern)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def shared_sqlite_engine():
    """An in-memory SQLite engine that shares a single connection across loops.

    Using StaticPool means every connect() returns the same DB-API connection.
    Combined with check_same_thread=False, this lets us drive the same in-memory
    database from successive event loops (the task spins up its own loop).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _run(_setup())

    yield engine

    _run(engine.dispose())


@pytest.fixture
def patched_async_session(monkeypatch, shared_sqlite_engine):
    """Patch `qualification_tasks.async_session` to bind to our test engine."""
    session_factory = async_sessionmaker(
        shared_sqlite_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(
        "src.tasks.qualification_tasks.async_session", session_factory
    )
    return session_factory


def _seed_restaurant_with_icp(session_factory):
    """Create a Restaurant + ICPScore that should qualify, return restaurant_id."""
    from src.db.models import ICPScore, Restaurant

    async def _seed():
        async with session_factory() as session:
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
            session.add(rest)
            await session.flush()

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
            session.add(icp)
            await session.commit()
            return rest.id

    return _run(_seed())


def test_qualify_restaurant_task_creates_qualification_result(patched_async_session):
    """Task runs end-to-end and inserts a QualificationResult row."""
    restaurant_id = _seed_restaurant_with_icp(patched_async_session)

    # Celery task is sync; .apply() runs it eagerly and we can .get()
    result = qualify_restaurant_task.apply(args=[str(restaurant_id)]).get()

    assert result["status"] in {"qualified", "needs_review", "not_qualified"}
    assert "qualification_result_id" in result
    assert "confidence" in result
    assert 0.0 <= result["confidence"] <= 1.0

    # Verify the QualificationResult row exists and matches the returned id
    async def _verify():
        from src.services.qualification import get_latest_qualification

        async with patched_async_session() as session:
            qr = await get_latest_qualification(session, restaurant_id)
            return qr

    qr = _run(_verify())
    assert qr is not None
    assert str(qr.id) == result["qualification_result_id"]


def test_qualify_restaurant_task_promotes_lead_when_qualified(patched_async_session):
    """When a lead_id is supplied and qualification passes, the Lead's
    lifecycle_stage and status are promoted to 'qualified'."""
    restaurant_id = _seed_restaurant_with_icp(patched_async_session)

    from src.db.models import Lead

    async def _seed_lead():
        async with patched_async_session() as session:
            lead = Lead(
                first_name="Owner",
                last_name="One",
                email="owner@testpizza.example",
                source="prospect_promotion",
                status="new",
                lifecycle_stage="prospect",
                restaurant_id=restaurant_id,
            )
            session.add(lead)
            await session.commit()
            return lead.id

    lead_id = _run(_seed_lead())

    result = qualify_restaurant_task.apply(
        args=[str(restaurant_id), str(lead_id)]
    ).get()

    assert result["status"] == "qualified"

    async def _fetch_lead():
        async with patched_async_session() as session:
            return await session.get(Lead, lead_id)

    refreshed = _run(_fetch_lead())
    assert refreshed is not None
    assert refreshed.lifecycle_stage == "qualified"
    assert refreshed.status == "qualified"
