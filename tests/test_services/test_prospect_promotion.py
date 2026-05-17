"""Tests for the prospect_promotion orchestrator service (NIF-284)."""

import pytest

from src.services.prospect_promotion import PromotionResult, promote_prospects


@pytest.mark.asyncio
async def test_promote_single_restaurant_creates_lead(
    db_session, sample_restaurant_with_icp_score
):
    result = await promote_prospects(
        db_session,
        restaurant_ids=[sample_restaurant_with_icp_score.id],
        campaign_id=None,
        new_campaign=None,
        owner="rep@example.com",
        notes=None,
        actor="rep@example.com",
    )

    assert isinstance(result, PromotionResult)
    assert result.promoted == 1
    assert result.skipped_already_lead == 0
    assert len(result.lead_ids) == 1
    assert result.campaign_id is None

    # Verify Lead exists with expected fields
    from sqlalchemy import select

    from src.db.models import Lead

    leads = (
        await db_session.execute(
            select(Lead).where(Lead.restaurant_id == sample_restaurant_with_icp_score.id)
        )
    ).scalars().all()
    assert len(leads) == 1
    lead = leads[0]
    assert lead.source == "prospect_finder"
    assert lead.owner == "rep@example.com"
    assert lead.status == "new"
    assert lead.lifecycle_stage == "new"
    assert lead.icp_total_score == 85.0
    assert lead.matched_restaurant_name == sample_restaurant_with_icp_score.name


@pytest.mark.asyncio
async def test_promote_skips_already_lead(db_session, sample_restaurant_with_icp_score):
    from src.db.models import Lead
    existing = Lead(
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
        source="manual",
        status="new",
        lifecycle_stage="new",
        restaurant_id=sample_restaurant_with_icp_score.id,
    )
    db_session.add(existing)
    await db_session.flush()

    result = await promote_prospects(
        db_session,
        restaurant_ids=[sample_restaurant_with_icp_score.id],
        campaign_id=None,
        new_campaign=None,
        owner=None,
        notes=None,
        actor="rep@example.com",
    )

    assert result.promoted == 0
    assert result.skipped_already_lead == 1


@pytest.mark.asyncio
async def test_promote_attaches_to_existing_campaign(
    db_session, sample_restaurant_with_icp_score
):
    from src.services.outreach import create_campaign, list_targets
    campaign = await create_campaign(db_session, name="Existing", campaign_type="email")
    await db_session.flush()

    result = await promote_prospects(
        db_session,
        restaurant_ids=[sample_restaurant_with_icp_score.id],
        campaign_id=campaign.id,
        new_campaign=None,
        owner=None,
        notes=None,
        actor="rep@example.com",
    )

    assert result.promoted == 1
    assert result.campaign_id == str(campaign.id)

    targets = await list_targets(db_session, campaign.id)
    assert len(targets) == 1
    assert targets[0].restaurant_id == sample_restaurant_with_icp_score.id
    assert str(targets[0].lead_id) == result.lead_ids[0]
