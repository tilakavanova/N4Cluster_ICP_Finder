"""Tests for the outreach dashboard routes and templates (NIF-284, Task E1)."""

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.db.session import get_session
from src.main import app


@pytest.fixture(autouse=True)
def _mock_qualify_delay(monkeypatch):
    """Stub `qualify_restaurant_task.delay` for every test in this module.

    Safety belt — these tests shouldn't trigger qualification dispatch, but
    if any indirect codepath does, the celery-eager autouse fixture would
    otherwise spin up an asyncio loop inside the pytest-asyncio loop.
    """
    stub_result = MagicMock()
    stub_result.id = "stub-task-id"
    monkeypatch.setattr(
        "src.tasks.qualification_tasks.qualify_restaurant_task.delay",
        MagicMock(return_value=stub_result),
    )


@pytest.fixture(autouse=True)
def _open_dashboard(monkeypatch):
    """Make dashboard routes open-access (no password) for these tests."""
    from src.config import settings

    monkeypatch.setattr(settings, "dashboard_password", "")


@pytest_asyncio.fixture
async def async_client(db_session):
    """HTTP client that drives the real FastAPI app with our in-memory DB."""

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
    """Marker fixture — `_open_dashboard` already disables the login gate."""
    return async_client


@pytest.mark.asyncio
async def test_campaign_detail_route_returns_200(async_client, db_session, logged_in_session):
    from src.services.outreach import create_campaign
    c = await create_campaign(db_session, name="X", campaign_type="email")
    await db_session.commit()

    response = await async_client.get(f"/dashboard/outreach/campaigns/{c.id}")
    assert response.status_code == 200
    assert "X" in response.text


@pytest.mark.asyncio
async def test_outreach_dashboard_uses_correct_detail_url(
    async_client, db_session, logged_in_session
):
    from src.services.outreach import create_campaign
    c = await create_campaign(db_session, name="DetailURL", campaign_type="email")
    await db_session.commit()

    response = await async_client.get("/dashboard/outreach")
    assert response.status_code == 200
    assert f"/dashboard/outreach/campaigns/{c.id}" in response.text
    # And NOT the broken /detail suffix
    assert f"/dashboard/outreach/campaigns/{c.id}/detail" not in response.text
