"""Service-level tests for outreach.create_campaign (NIF-284)."""

import pytest

from src.services.outreach import add_target, create_campaign, list_targets


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


@pytest.mark.asyncio
async def test_list_targets_eager_loads_restaurant_and_lead(db_session, sample_restaurant, sample_lead):
    campaign = await create_campaign(db_session, name="X", campaign_type="email")
    await add_target(db_session, campaign.id, sample_restaurant.id, lead_id=sample_lead.id)
    await db_session.flush()

    # Capture identifiers before expire_all so we don't trigger sync refresh
    # on the parent instances when reading their attributes below.
    campaign_id = campaign.id
    expected_name = sample_restaurant.name
    expected_lead_id = sample_lead.id

    db_session.expire_all()  # force fresh load — confirms eager-loading works
    targets = await list_targets(db_session, campaign_id)

    assert len(targets) == 1
    # Accessing without an extra query — if not eager-loaded these would be lazy and fail
    # under expire_all + sync access
    assert targets[0].restaurant.name == expected_name
    assert targets[0].lead.id == expected_lead_id
