"""Email send service with tracking (NIF-226).

Orchestration layer that ties together SendGridClient, communication_status,
tracking tokens, and the outreach activity log.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import Lead, OutreachTarget
from src.services.communication_status import mark_as_failed, mark_as_opted_out, mark_as_sent
from src.services.outreach import log_activity
from src.services.sendgrid_client import SendGridClient
from src.utils.logging import get_logger

logger = get_logger("email_service")

# Free-tier daily send cap — enforced per send_bulk_outreach call
DAILY_SEND_LIMIT = 100


def _build_sendgrid_client(redis_client=None) -> SendGridClient:
    return SendGridClient(
        api_key=settings.sendgrid_api_key,
        from_email=settings.sendgrid_from_email,
        from_name=settings.sendgrid_from_name,
        tracking_base_url=settings.tracking_base_url,
        redis_client=redis_client,
    )


async def send_outreach_email(
    session: AsyncSession,
    target_id: UUID,
    lead_id: UUID,
    campaign_id: UUID,
    subject: str,
    html_content: str,
    text_content: str | None = None,
    redis_client=None,
) -> dict[str, Any]:
    """Send a single outreach email with full tracking.

    Flow:
    1. Check lead.email_opt_out → OPTED_OUT if true
    2. Validate lead email address
    3. Call SendGridClient.send_email() (URL wrapping + pixel injection handled internally)
    4. On success: log OutreachActivity, store external_message_id, mark_as_sent
    5. On failure: mark_as_failed

    Returns:
        dict with keys: status ("sent"|"failed"|"opted_out"), message_id, error
    """
    # 1. Load lead and check opt-out
    lead = await session.get(Lead, lead_id)
    if lead is None:
        logger.warning("email_send_lead_not_found", lead_id=str(lead_id))
        return {"status": "failed", "message_id": None, "error": "Lead not found"}

    if lead.email_opt_out:
        logger.info("email_send_opted_out", lead_id=str(lead_id), target_id=str(target_id))
        await mark_as_opted_out(session, target_id, "email")
        await session.commit()
        return {"status": "opted_out", "message_id": None, "error": None}

    # 2. Validate target + email address
    target = await session.get(OutreachTarget, target_id)
    if target is None:
        logger.warning("email_send_target_not_found", target_id=str(target_id))
        return {"status": "failed", "message_id": None, "error": "Target not found"}

    to_email = lead.email
    if not to_email:
        error = "Lead has no email address"
        logger.warning("email_send_no_email", lead_id=str(lead_id))
        await mark_as_failed(session, target_id, "email", error_reason=error)
        await session.commit()
        return {"status": "failed", "message_id": None, "error": error}

    # 3. Send via SendGrid
    tracking_data = {
        "lead_id": str(lead_id),
        "campaign_id": str(campaign_id),
        "target_id": str(target_id),
    }
    client = _build_sendgrid_client(redis_client=redis_client)
    success, message_id, error = client.send_email(
        to_email=to_email,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        tracking_data=tracking_data,
    )

    if success:
        # 4. Log activity and store external_message_id
        activity = await log_activity(
            session,
            target_id=target_id,
            activity_type="email_sent",
            performed_by="system",
        )
        activity.external_message_id = message_id
        activity.channel = "email"

        await mark_as_sent(session, target_id, "email", external_message_id=message_id)
        await session.commit()

        logger.info(
            "email_send_success",
            target_id=str(target_id),
            message_id=message_id,
        )
        return {"status": "sent", "message_id": message_id, "error": None}

    # 5. On failure
    await mark_as_failed(session, target_id, "email", error_reason=error)
    await session.commit()

    logger.error(
        "email_send_failed",
        target_id=str(target_id),
        error=error,
    )
    return {"status": "failed", "message_id": None, "error": error}


async def send_bulk_outreach(
    session: AsyncSession,
    campaign_id: UUID,
    subject: str,
    html_template: str,
    targets: list[dict],
    redis_client=None,
) -> dict[str, Any]:
    """Send outreach emails to multiple targets.

    Each entry in ``targets`` must contain:
        target_id, lead_id, email, personalization_data (optional dict)

    Rate-limited to DAILY_SEND_LIMIT emails per invocation (free tier).

    Returns:
        {
            "sent": N,
            "failed": N,
            "opted_out": N,
            "rate_limited": N,
            "details": [{"target_id": ..., "status": ..., "message_id": ..., "error": ...}]
        }
    """
    sent = 0
    failed = 0
    opted_out = 0
    rate_limited = 0
    details: list[dict] = []

    for target_data in targets:
        target_id = UUID(str(target_data["target_id"]))
        lead_id = UUID(str(target_data["lead_id"]))
        personalization = target_data.get("personalization_data", {}) or {}

        # Rate limit guard
        if sent >= DAILY_SEND_LIMIT:
            rate_limited += 1
            details.append({
                "target_id": str(target_id),
                "status": "rate_limited",
                "message_id": None,
                "error": "Daily send limit reached",
            })
            continue

        # Apply simple {{key}} personalisation to the template
        html_content = html_template
        for key, value in personalization.items():
            html_content = html_content.replace(f"{{{{{key}}}}}", str(value))

        result = await send_outreach_email(
            session=session,
            target_id=target_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            subject=subject,
            html_content=html_content,
            redis_client=redis_client,
        )

        details.append({
            "target_id": str(target_id),
            "status": result["status"],
            "message_id": result.get("message_id"),
            "error": result.get("error"),
        })

        if result["status"] == "sent":
            sent += 1
        elif result["status"] == "opted_out":
            opted_out += 1
        else:
            failed += 1

    logger.info(
        "bulk_outreach_complete",
        campaign_id=str(campaign_id),
        sent=sent,
        failed=failed,
        opted_out=opted_out,
        rate_limited=rate_limited,
    )
    return {
        "sent": sent,
        "failed": failed,
        "opted_out": opted_out,
        "rate_limited": rate_limited,
        "details": details,
    }
