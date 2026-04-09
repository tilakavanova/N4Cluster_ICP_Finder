"""Lead routing and notification rules.

Routes leads to different notification channels based on ICP fit:
- Hot (Excellent + multi-location): immediate Slack webhook + priority email
- Warm (Good): standard email with enrichment
- Cold (Moderate/Poor): batched for daily digest
- Newsletter: no alert (HubSpot-only)
"""

import httpx

from src.config import settings
from src.db.models import Lead
from src.utils.logging import get_logger

logger = get_logger("lead_notifications")


def classify_lead(lead: Lead) -> str:
    """Classify lead as hot, warm, or cold based on ICP score and attributes."""
    if lead.source == "website_newsletter":
        return "newsletter"

    score = lead.icp_total_score or 0
    is_multi = lead.locations and lead.locations not in ("1", "")

    if score >= settings.hot_lead_threshold and is_multi:
        return "hot"
    if lead.icp_fit_label == "excellent":
        return "hot"
    if score >= settings.warm_lead_threshold or lead.icp_fit_label == "good":
        return "warm"
    return "cold"


def _build_lead_summary(lead: Lead) -> str:
    """Build human-readable lead summary."""
    lines = [
        f"Name: {lead.first_name} {lead.last_name}",
        f"Email: {lead.email}",
    ]
    if lead.company:
        lines.append(f"Company: {lead.company}")
    if lead.business_type:
        lines.append(f"Type: {lead.business_type}")
    if lead.locations:
        lines.append(f"Locations: {lead.locations}")
    if lead.interest:
        lines.append(f"Interest: {lead.interest}")
    if lead.icp_fit_label:
        lines.append(f"ICP Fit: {lead.icp_fit_label} ({lead.icp_total_score:.0f})")
    if lead.matched_restaurant_name:
        lines.append(f"Matched: {lead.matched_restaurant_name}")
    if lead.has_delivery is not None:
        lines.append(f"Delivery: {lead.has_delivery}")
    if lead.is_independent is not None:
        lines.append(f"Independent: {lead.is_independent}")
    if lead.message:
        lines.append(f"Message: {lead.message[:200]}")
    return "\n".join(lines)


async def send_slack_alert(lead: Lead, tier: str) -> bool:
    """Send lead alert to Slack via webhook."""
    if not settings.slack_webhook_url:
        return False

    emoji = {"hot": ":fire:", "warm": ":sunny:", "cold": ":snowflake:"}.get(tier, ":bell:")
    summary = _build_lead_summary(lead)

    payload = {
        "text": f"{emoji} *{tier.upper()} Lead* — {lead.first_name} {lead.last_name} ({lead.company or 'N/A'})",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {tier.upper()} Lead"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```\n{summary}\n```"},
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.slack_webhook_url, json=payload)
            if resp.status_code == 200:
                logger.info("slack_alert_sent", tier=tier, lead_id=str(lead.id))
                return True
            logger.error("slack_alert_failed", status=resp.status_code)
    except Exception as e:
        logger.error("slack_alert_error", error=str(e))
    return False


async def send_email_alert(lead: Lead, tier: str) -> bool:
    """Send lead alert email via Gmail API (reuses existing OAuth setup).

    For now, logs the intent — actual Gmail sending requires OAuth credentials
    which are on the website side. This creates a notification record.
    """
    if not settings.alert_email:
        return False

    # In production, this would use Gmail API or SMTP.
    # For now, we log the notification for the daily digest task to pick up.
    logger.info(
        "email_alert_queued",
        tier=tier,
        lead_id=str(lead.id),
        recipient=settings.alert_email,
        company=lead.company,
        fit=lead.icp_fit_label,
    )
    return True


async def route_lead(lead: Lead) -> dict:
    """Route a lead to the appropriate notification channels.

    Returns dict with tier and notification results.
    """
    tier = classify_lead(lead)

    result = {"tier": tier, "slack": False, "email": False}

    if tier == "newsletter":
        logger.info("lead_routed", tier=tier, lead_id=str(lead.id), action="hubspot_only")
        return result

    if tier == "hot":
        result["slack"] = await send_slack_alert(lead, tier)
        result["email"] = await send_email_alert(lead, tier)
    elif tier == "warm":
        result["email"] = await send_email_alert(lead, tier)
    else:
        # Cold — batched for daily digest, no immediate alert
        logger.info("lead_routed", tier=tier, lead_id=str(lead.id), action="daily_digest")

    logger.info("lead_routed", tier=tier, lead_id=str(lead.id), notifications=result)
    return result
