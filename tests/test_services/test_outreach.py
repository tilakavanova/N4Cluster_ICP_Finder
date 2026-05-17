"""Service-level tests for outreach.create_campaign (NIF-284)."""

import pytest

from src.services.outreach import create_campaign


@pytest.mark.asyncio
async def test_create_campaign_with_active_status(db_session):
    campaign = await create_campaign(
        db_session,
        name="Test Active",
        campaign_type="email",
        status="active",
    )
    assert campaign.status == "active"


@pytest.mark.asyncio
async def test_create_campaign_defaults_to_draft(db_session):
    campaign = await create_campaign(db_session, name="Test Draft", campaign_type="email")
    assert campaign.status == "draft"


@pytest.mark.asyncio
async def test_create_campaign_rejects_invalid_status(db_session):
    with pytest.raises(ValueError, match="Invalid status"):
        await create_campaign(db_session, name="X", campaign_type="email", status="bogus")
