"""SMS send service via Plivo with delivery callbacks (NIF-231).

Orchestration layer that sends SMS messages through the Plivo REST API,
integrates TCPA compliance checks (NIF-234), replaces URLs with short
tracking URLs (NIF-233), and logs TrackerEvents for delivery tracking.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import Lead, OutreachTarget, TrackerEvent
from src.services.communication_status import mark_as_failed, mark_as_opted_out, mark_as_sent
from src.services.outreach import log_activity
from src.services.tcpa import can_send_sms
from src.services.url_shortener import replace_urls_in_message
from src.utils.logging import get_logger

logger = get_logger("sms_service")

# Free-tier daily send cap
DAILY_SMS_LIMIT = 100

# Plivo API base
PLIVO_API_BASE = "https://api.plivo.com/v1"


class SMSService:
    """Send SMS messages via Plivo REST API with full tracking."""

    def __init__(
        self,
        auth_id: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
        callback_url: str | None = None,
        redis_client: Any = None,
    ) -> None:
        self.auth_id = auth_id or settings.plivo_auth_id
        self.auth_token = auth_token or settings.plivo_auth_token
        self.from_number = from_number or settings.plivo_from_number
        self.callback_url = callback_url or settings.plivo_callback_url
        self._redis = redis_client

    @property
    def _plivo_url(self) -> str:
        return f"{PLIVO_API_BASE}/Account/{self.auth_id}/Message/"

    async def send_sms(
        self,
        session: AsyncSession,
        to_number: str,
        message: str,
        lead_id: UUID,
        campaign_id: UUID,
        target_id: UUID | None = None,
        timezone_str: str | None = None,
    ) -> dict[str, Any]:
        """Send a single SMS with TCPA compliance and click tracking.

        Flow:
        1. TCPA gate — check consent + quiet hours
        2. Replace URLs with short tracking URLs
        3. Call Plivo REST API
        4. Log TrackerEvent and OutreachActivity
        5. Update communication status

        Returns:
            dict with keys: status, message_uuid, error
        """
        # 1. TCPA compliance gate
        allowed, reason = await can_send_sms(session, to_number, timezone_str)
        if not allowed:
            logger.info(
                "sms_blocked",
                to=to_number[-4:],
                reason=reason,
                lead_id=str(lead_id),
            )
            if reason == "no_consent" and target_id:
                await mark_as_opted_out(session, target_id, "sms")
                await session.commit()
            return {"status": "blocked", "message_uuid": None, "error": reason}

        # 2. Replace URLs with tracked short URLs
        tracked_message = replace_urls_in_message(
            message,
            lead_id=str(lead_id),
            campaign_id=str(campaign_id),
            target_id=str(target_id) if target_id else "",
            redis_client=self._redis,
        )

        # 3. Send via Plivo
        callback_url = self.callback_url or f"{settings.tracking_base_url}/api/v1/sms/callback"
        payload = {
            "src": self.from_number,
            "dst": to_number,
            "text": tracked_message,
            "url": callback_url,
            "method": "POST",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._plivo_url,
                    json=payload,
                    auth=(self.auth_id, self.auth_token),
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            error_msg = f"Plivo API error: {exc.response.status_code} {exc.response.text}"
            logger.error("sms_send_plivo_error", error=error_msg, to=to_number[-4:])
            if target_id:
                await mark_as_failed(session, target_id, "sms", error_reason=error_msg)
                await session.commit()
            return {"status": "failed", "message_uuid": None, "error": error_msg}
        except Exception as exc:
            error_msg = f"SMS send error: {str(exc)}"
            logger.error("sms_send_error", error=error_msg, to=to_number[-4:])
            if target_id:
                await mark_as_failed(session, target_id, "sms", error_reason=error_msg)
                await session.commit()
            return {"status": "failed", "message_uuid": None, "error": error_msg}

        # Extract message UUID from Plivo response
        message_uuid = None
        message_uuids = data.get("message_uuid") or []
        if isinstance(message_uuids, list) and message_uuids:
            message_uuid = message_uuids[0]
        elif isinstance(message_uuids, str):
            message_uuid = message_uuids

        # 4. Log TrackerEvent
        tracker = TrackerEvent(
            event_type="delivery",
            channel="sms",
            lead_id=lead_id,
            campaign_id=campaign_id,
            target_id=target_id,
            provider="plivo",
            provider_event_id=message_uuid,
            event_metadata={"status": "sent", "to": to_number},
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(tracker)

        # Log OutreachActivity
        if target_id:
            activity = await log_activity(
                session,
                target_id=target_id,
                activity_type="sms_sent",
                performed_by="system",
            )
            activity.external_message_id = message_uuid
            activity.channel = "sms"

            await mark_as_sent(session, target_id, "sms", external_message_id=message_uuid)

        await session.commit()

        logger.info(
            "sms_send_success",
            to=to_number[-4:],
            message_uuid=message_uuid,
            lead_id=str(lead_id),
        )
        return {"status": "sent", "message_uuid": message_uuid, "error": None}

    async def send_bulk_sms(
        self,
        session: AsyncSession,
        campaign_id: UUID,
        message_template: str,
        targets: list[dict],
    ) -> dict[str, Any]:
        """Send SMS to multiple targets from a campaign.

        Each entry in ``targets`` must contain:
            target_id, lead_id, phone_number, timezone (optional),
            personalization_data (optional dict)

        Returns:
            Summary dict with sent/failed/blocked/rate_limited counts.
        """
        sent = 0
        failed = 0
        blocked = 0
        rate_limited = 0
        details: list[dict] = []

        for target_data in targets:
            t_id = UUID(str(target_data["target_id"]))
            l_id = UUID(str(target_data["lead_id"]))
            phone = target_data["phone_number"]
            tz_str = target_data.get("timezone")
            personalization = target_data.get("personalization_data", {}) or {}

            # Rate limit guard
            if sent >= DAILY_SMS_LIMIT:
                rate_limited += 1
                details.append({
                    "target_id": str(t_id),
                    "status": "rate_limited",
                    "message_uuid": None,
                    "error": "Daily SMS limit reached",
                })
                continue

            # Apply simple {{key}} personalisation
            msg = message_template
            for key, value in personalization.items():
                msg = msg.replace(f"{{{{{key}}}}}", str(value))

            result = await self.send_sms(
                session=session,
                to_number=phone,
                message=msg,
                lead_id=l_id,
                campaign_id=campaign_id,
                target_id=t_id,
                timezone_str=tz_str,
            )

            details.append({
                "target_id": str(t_id),
                "status": result["status"],
                "message_uuid": result.get("message_uuid"),
                "error": result.get("error"),
            })

            if result["status"] == "sent":
                sent += 1
            elif result["status"] == "blocked":
                blocked += 1
            else:
                failed += 1

        logger.info(
            "bulk_sms_complete",
            campaign_id=str(campaign_id),
            sent=sent,
            failed=failed,
            blocked=blocked,
            rate_limited=rate_limited,
        )
        return {
            "sent": sent,
            "failed": failed,
            "blocked": blocked,
            "rate_limited": rate_limited,
            "details": details,
        }


async def handle_delivery_callback(
    session: AsyncSession,
    callback_data: dict,
) -> None:
    """Process a Plivo delivery status callback.

    Plivo POSTs delivery receipts with fields:
        MessageUUID, Status, From, To, ErrorCode, etc.

    Maps Plivo statuses to TrackerEvent event_types and updates
    communication status accordingly.
    """
    message_uuid = callback_data.get("MessageUUID") or callback_data.get("message_uuid")
    status = (callback_data.get("Status") or callback_data.get("status", "")).lower()

    if not message_uuid:
        logger.warning("delivery_callback_no_uuid", data=callback_data)
        return

    # Map Plivo status to our event types
    status_map = {
        "queued": "delivery",
        "sent": "delivery",
        "delivered": "delivery",
        "undelivered": "bounce",
        "failed": "bounce",
        "rejected": "bounce",
    }
    event_type = status_map.get(status, "delivery")

    # Deduplicate — use MessageUUID + status as provider_event_id
    provider_event_id = f"plivo:{message_uuid}:{status}"

    tracker = TrackerEvent(
        event_type=event_type,
        channel="sms",
        provider="plivo",
        provider_event_id=provider_event_id,
        event_metadata=callback_data,
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(tracker)

    try:
        await session.commit()
    except Exception:
        # Duplicate provider_event_id — already processed
        await session.rollback()
        logger.debug("delivery_callback_duplicate", uuid=message_uuid, status=status)
        return

    logger.info(
        "delivery_callback_processed",
        message_uuid=message_uuid,
        status=status,
        event_type=event_type,
    )
