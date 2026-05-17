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


@pytest.mark.asyncio
async def test_outreach_dashboard_renders_response_rate_column(
    async_client, db_session, logged_in_session
):
    from src.services.outreach import create_campaign
    await create_campaign(db_session, name="Stats Test", campaign_type="email")
    await db_session.commit()

    response = await async_client.get("/dashboard/outreach")
    assert response.status_code == 200
    assert "Response Rate" in response.text
    assert "Conversion" in response.text


@pytest.mark.asyncio
async def test_create_campaign_form_persists_start_date(
    async_client, db_session, logged_in_session
):
    response = await async_client.post(
        "/dashboard/outreach/campaigns",
        data={
            "name": "Dated Campaign",
            "campaign_type": "email",
            "start_date": "2026-06-01",
            "end_date": "2026-06-30",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    from sqlalchemy import select
    from src.db.models import OutreachCampaign
    campaign = (await db_session.execute(
        select(OutreachCampaign).where(OutreachCampaign.name == "Dated Campaign")
    )).scalar_one()
    assert campaign.start_date is not None
    assert campaign.start_date.month == 6
    assert campaign.end_date.day == 30


@pytest.mark.asyncio
async def test_campaign_detail_shows_restaurant_name_and_lead_link(
    async_client, db_session, logged_in_session, sample_restaurant_with_icp_score
):
    from src.services.outreach import create_campaign, add_target
    from src.db.models import Lead
    c = await create_campaign(db_session, name="DetailTest", campaign_type="email")
    lead = Lead(
        source="manual", status="new", lifecycle_stage="new",
        restaurant_id=sample_restaurant_with_icp_score.id,
    )
    db_session.add(lead)
    await db_session.flush()
    await add_target(db_session, c.id, sample_restaurant_with_icp_score.id, lead_id=lead.id)
    await db_session.commit()

    response = await async_client.get(f"/dashboard/outreach/campaigns/{c.id}")
    assert response.status_code == 200
    # Restaurant name appears
    assert sample_restaurant_with_icp_score.name in response.text
    # Lead link appears
    assert f"/dashboard/leads/{lead.id}" in response.text


@pytest.mark.asyncio
async def test_status_patch_returns_rendered_pill(
    async_client, db_session, logged_in_session
):
    from src.services.outreach import create_campaign
    c = await create_campaign(db_session, name="StatusTest", campaign_type="email")
    await db_session.commit()

    response = await async_client.patch(
        f"/dashboard/outreach/campaigns/{c.id}/status",
        data={"status": "active"},
    )
    assert response.status_code == 200
    assert "Active" in response.text
    # The response should contain a styled pill (e.g., bg-green-50 class)
    assert "bg-green" in response.text or "green" in response.text
