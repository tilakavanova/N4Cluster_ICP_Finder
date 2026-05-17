"""Tests for POST /dashboard/prospects/promote (NIF-284, Task C1)."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.db.session import get_session
from src.main import app


@pytest.fixture(autouse=True)
def _mock_qualify_delay(monkeypatch):
    """Stub `qualify_restaurant_task.delay` for every test in this module.

    With the autouse `celery_eager` fixture from conftest, `.delay()` would
    run the task body synchronously, but the task implementation spins up its
    own asyncio loop that cannot run inside the pytest-asyncio loop already
    driving these tests. Returning a MagicMock id sidesteps that.
    """
    stub_result = MagicMock()
    stub_result.id = "stub-task-id"
    monkeypatch.setattr(
        "src.tasks.qualification_tasks.qualify_restaurant_task.delay",
        MagicMock(return_value=stub_result),
    )


@pytest.fixture(autouse=True)
def _open_dashboard(monkeypatch):
    """Make dashboard routes open-access (no password) for promotion tests
    that need to skip the login challenge. Tests that exercise the redirect
    explicitly re-set `dashboard_password` to a non-empty value first.
    """
    from src.config import settings

    monkeypatch.setattr(settings, "dashboard_password", "")


@pytest_asyncio.fixture
async def async_client(db_session):
    """HTTP client that drives the real FastAPI app with our in-memory DB.

    Overrides the `get_session` dependency so route handlers run against the
    same SQLite session the test fixtures populated. The dashboard route
    calls `session.commit()` — SQLite-memory tolerates it, and the outer
    `db_session` fixture's rollback at teardown is a no-op afterwards.
    """

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def logged_in_session(async_client):
    """Marker fixture — `_open_dashboard` already disables the login gate, so
    no real login round-trip is needed. Kept as a separate fixture so test
    signatures match the task spec.
    """
    return async_client


async def test_promote_single_restaurant_returns_toast_and_row(
    async_client, sample_restaurant_with_icp_score, logged_in_session
):
    response = await async_client.post(
        "/dashboard/prospects/promote",
        data={
            "restaurant_ids": [str(sample_restaurant_with_icp_score.id)],
            "campaign_id": "",
            "new_campaign_name": "",
            "owner": "",
            "notes": "",
        },
    )
    assert response.status_code == 200, response.text
    body = response.text
    assert "Promoted" in body
    assert "<strong>1</strong>" in body
    # Row partial updated for the promoted restaurant
    assert "Already a Lead" in body or str(sample_restaurant_with_icp_score.id) in body


async def test_promote_unauthenticated_redirects(async_client, monkeypatch):
    """When dashboard_password is set and there is no session, expect a 303
    redirect to the login page (mirrors the behaviour of other dashboard
    routes via `_require_login`).
    """
    from src.config import settings

    monkeypatch.setattr(settings, "dashboard_password", "secret-for-test")

    response = await async_client.post(
        "/dashboard/prospects/promote",
        data={"restaurant_ids": []},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/dashboard/login" in response.headers["location"]


async def test_promote_rejects_bulk_over_cap(async_client, logged_in_session):
    ids = [str(uuid4()) for _ in range(101)]
    # Repeat the same form key 101 times — FastAPI binds the resulting list
    # to `restaurant_ids: list[str]`. httpx accepts a dict-of-list payload
    # for this URL-encoded form shape.
    response = await async_client.post(
        "/dashboard/prospects/promote",
        data={"restaurant_ids": ids},
    )
    assert response.status_code == 400
    assert "100 or fewer" in response.text


@pytest.mark.asyncio
async def test_qualification_card_renders_for_lead_with_qualified_result(
    async_client, db_session, sample_restaurant_with_icp_score, logged_in_session
):
    """Seed Lead + QualificationResult directly, then fetch the polling endpoint."""
    from datetime import datetime, timezone

    from src.db.models import Lead, QualificationResult

    lead = Lead(
        source="prospect_finder",
        status="qualified",
        lifecycle_stage="qualified",
        restaurant_id=sample_restaurant_with_icp_score.id,
    )
    db_session.add(lead)
    await db_session.flush()

    qual = QualificationResult(
        restaurant_id=sample_restaurant_with_icp_score.id,
        qualification_status="qualified",
        confidence_score=0.92,
        signals_summary=["ICP-fit", "delivery-on"],
        qualified_at=datetime.now(timezone.utc),
    )
    db_session.add(qual)
    await db_session.commit()

    response = await async_client.get(f"/dashboard/leads/{lead.id}/qualification")
    assert response.status_code == 200, response.text
    body = response.text
    # Status pill renders correctly
    assert "Qualified" in body
    # Confidence rendered
    assert "92%" in body
    # No polling attribute since this is a final state
    assert 'hx-trigger="every 3s"' not in body


@pytest.mark.asyncio
async def test_qualification_card_shows_qualifying_state_when_no_result(
    async_client, db_session, sample_restaurant_with_icp_score, logged_in_session
):
    """Lead exists but no QualificationResult yet — card shows 'Qualifying…' and polls."""
    from src.db.models import Lead

    lead = Lead(
        source="prospect_finder",
        status="new",
        lifecycle_stage="new",
        restaurant_id=sample_restaurant_with_icp_score.id,
    )
    db_session.add(lead)
    await db_session.commit()

    response = await async_client.get(f"/dashboard/leads/{lead.id}/qualification")
    assert response.status_code == 200, response.text
    body = response.text
    assert "Qualifying" in body
    # Polling attribute present since result is not final
    assert 'hx-trigger="every 3s"' in body
