"""Tests for the prospect_promotion orchestrator service (NIF-284)."""

from unittest.mock import MagicMock

import pytest

from src.services.prospect_promotion import NewCampaignSpec, PromotionResult, promote_prospects


@pytest.fixture(autouse=True)
def _mock_qualify_delay(monkeypatch):
    """Stub `qualify_restaurant_task.delay` for all tests in this module.

    `promote_prospects` now dispatches a Celery task per promoted Lead. With the
    `celery_eager` autouse fixture from conftest, `.delay()` would run the task
    body synchronously — but the task uses `asyncio.new_event_loop()` internally,
    which cannot run inside the pytest-asyncio loop already running these tests.
    Stubbing `.delay()` to return a MagicMock id sidesteps that. Tests that
    specifically assert on dispatch (`test_promote_dispatches_qualification`)
    re-monkeypatch with their own tracked mock to inspect call args.
    """
    stub_result = MagicMock()
    stub_result.id = "stub-task-id"
    monkeypatch.setattr(
        "src.tasks.qualification_tasks.qualify_restaurant_task.delay",
        MagicMock(return_value=stub_result),
    )


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


@pytest.mark.asyncio
async def test_promote_creates_new_campaign_inline(
    db_session, sample_restaurant_with_icp_score
):
    from src.services.outreach import list_campaigns, list_targets
    spec = NewCampaignSpec(name="Boston Pilot May", campaign_type="email", status="draft")

    result = await promote_prospects(
        db_session,
        restaurant_ids=[sample_restaurant_with_icp_score.id],
        campaign_id=None,
        new_campaign=spec,
        owner=None,
        notes=None,
        actor="rep@example.com",
    )

    assert result.promoted == 1
    assert result.campaign_id is not None

    campaigns = await list_campaigns(db_session)
    matched = [c for c in campaigns if c.name == "Boston Pilot May"]
    assert len(matched) == 1
    assert matched[0].status == "draft"

    targets = await list_targets(db_session, matched[0].id)
    assert len(targets) == 1


@pytest.mark.asyncio
async def test_promote_dispatches_qualification(
    db_session, sample_restaurant_with_icp_score, monkeypatch
):
    """Promotion dispatches a qualify_restaurant_task per promoted Lead.

    The Celery task in `src.tasks.qualification_tasks` uses its own sessionmaker
    (`async_session` bound to DATABASE_URL) and spins up its own event loop via
    `asyncio.new_event_loop()`. Running it for real from inside this pytest-asyncio
    test would (a) attempt to connect to a different DB than `db_session` and
    (b) collide with the already-running event loop. So we patch the task's
    `.delay()` to return a MagicMock with a stable id and assert dispatch happened.
    A separate sync test in tests/test_tasks/test_qualification_tasks.py exercises
    the end-to-end task path (shared StaticPool engine + fresh-loop pattern).
    """
    fake_async_result = MagicMock()
    fake_async_result.id = "fake-task-id-001"
    delay_mock = MagicMock(return_value=fake_async_result)
    monkeypatch.setattr(
        "src.tasks.qualification_tasks.qualify_restaurant_task.delay",
        delay_mock,
    )

    result = await promote_prospects(
        db_session,
        restaurant_ids=[sample_restaurant_with_icp_score.id],
        campaign_id=None,
        new_campaign=None,
        owner=None,
        notes=None,
        actor="rep@example.com",
    )

    assert result.promoted == 1
    assert len(result.qualification_task_ids) == 1
    assert result.qualification_task_ids[0] == "fake-task-id-001"
    assert result.reused_qualifications == 0

    # Dispatch was called with the restaurant id and the created lead id
    delay_mock.assert_called_once()
    call_args = delay_mock.call_args
    assert call_args.args[0] == str(sample_restaurant_with_icp_score.id)
    assert call_args.args[1] == result.lead_ids[0]


@pytest.mark.asyncio
async def test_promote_reuses_existing_qualification(
    db_session, sample_restaurant_with_icp_score, monkeypatch
):
    """If a non-expired qualified result exists, no new dispatch."""
    from datetime import datetime, timezone, timedelta

    from src.db.models import QualificationResult

    existing = QualificationResult(
        restaurant_id=sample_restaurant_with_icp_score.id,
        qualification_status="qualified",
        confidence_score=0.9,
        signals_summary=[],
        qualified_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(existing)
    await db_session.flush()

    # Guard against accidental dispatch — if reuse logic is broken, the test
    # should fail loudly instead of silently invoking the real Celery task.
    delay_mock = MagicMock()
    monkeypatch.setattr(
        "src.tasks.qualification_tasks.qualify_restaurant_task.delay",
        delay_mock,
    )

    result = await promote_prospects(
        db_session,
        restaurant_ids=[sample_restaurant_with_icp_score.id],
        campaign_id=None,
        new_campaign=None,
        owner=None,
        notes=None,
        actor="rep@example.com",
    )
    assert result.promoted == 1
    assert result.reused_qualifications == 1
    assert len(result.qualification_task_ids) == 0
    delay_mock.assert_not_called()

    # The Lead should have been advanced to "qualified" because the reused
    # result was status="qualified".
    from sqlalchemy import select

    from src.db.models import Lead

    lead = (
        await db_session.execute(
            select(Lead).where(Lead.restaurant_id == sample_restaurant_with_icp_score.id)
        )
    ).scalar_one()
    assert lead.lifecycle_stage == "qualified"
    assert lead.status == "qualified"


@pytest.mark.asyncio
async def test_promote_bulk_mixed_outcomes(db_session):
    """5 restaurants: 3 fresh promote, 1 already-Lead, 1 missing."""
    from uuid import uuid4
    from src.db.models import Restaurant, ICPScore, Lead

    fresh = []
    for i in range(3):
        r = Restaurant(name=f"Fresh {i}", address=f"{i} St", city="Boston", state="MA")
        db_session.add(r)
        await db_session.flush()
        db_session.add(ICPScore(restaurant_id=r.id, total_icp_score=70.0, fit_label="good"))
        fresh.append(r)

    already = Restaurant(name="Already", address="X St", city="Boston", state="MA")
    db_session.add(already)
    await db_session.flush()
    db_session.add(Lead(
        source="manual", status="new", lifecycle_stage="new", restaurant_id=already.id,
    ))
    await db_session.flush()

    missing_id = uuid4()

    result = await promote_prospects(
        db_session,
        restaurant_ids=[r.id for r in fresh] + [already.id, missing_id],
        campaign_id=None,
        new_campaign=None,
        owner=None,
        notes=None,
        actor="rep@example.com",
    )

    assert result.promoted == 3
    assert result.skipped_already_lead == 1
    assert len(result.failed) == 1
    assert str(missing_id) in result.failed[0]["restaurant_id"]
