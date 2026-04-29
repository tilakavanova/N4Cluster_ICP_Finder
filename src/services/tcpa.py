"""TCPA compliance layer for SMS communications (NIF-234).

Provides consent verification, quiet-hours enforcement, opt-in/opt-out
recording, and a combined ``can_send_sms`` gate used by the SMS send flow.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import SMSConsent
from src.utils.logging import get_logger

logger = get_logger("tcpa")

# TCPA quiet-hours window: before 8 AM or after 9 PM in the recipient's local timezone
QUIET_HOUR_START = 21  # 9 PM
QUIET_HOUR_END = 8     # 8 AM


def check_quiet_hours(timezone_str: str | None) -> bool:
    """Return True if the current time falls within TCPA quiet hours.

    Quiet hours are defined as before 8 AM or at/after 9 PM in the
    recipient's local timezone.  If timezone_str is None or invalid,
    defaults to UTC.

    Returns:
        True → quiet hours (do NOT send), False → safe to send.
    """
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone_str) if timezone_str else timezone.utc
    except Exception:
        tz = timezone.utc

    local_now = datetime.now(tz)
    hour = local_now.hour
    is_quiet = hour >= QUIET_HOUR_START or hour < QUIET_HOUR_END
    logger.debug(
        "quiet_hours_check",
        timezone=timezone_str,
        local_hour=hour,
        is_quiet=is_quiet,
    )
    return is_quiet


async def check_consent(session: AsyncSession, phone_number: str) -> bool:
    """Verify that an active opt-in consent record exists for the phone number.

    Returns:
        True → consented (may send), False → not consented (do NOT send).
    """
    result = await session.execute(
        select(SMSConsent).where(
            SMSConsent.phone_number == phone_number,
            SMSConsent.consent_type == "opt_in",
            SMSConsent.is_active.is_(True),
        )
    )
    consent = result.scalars().first()
    has_consent = consent is not None
    logger.debug("consent_check", phone_number=phone_number[-4:], has_consent=has_consent)
    return has_consent


async def record_consent(
    session: AsyncSession,
    phone_number: str,
    consent_type: str = "opt_in",
    source: str = "api",
) -> SMSConsent:
    """Record or update SMS consent for a phone number.

    If a record already exists for the phone number it is updated in place;
    otherwise a new record is created.

    Args:
        phone_number: E.164 formatted phone number.
        consent_type: "opt_in" or "opt_out".
        source: Origin of consent (e.g. "web_form", "sms_keyword", "api").

    Returns:
        The created / updated SMSConsent instance.
    """
    result = await session.execute(
        select(SMSConsent).where(SMSConsent.phone_number == phone_number)
    )
    existing = result.scalars().first()

    if existing:
        existing.consent_type = consent_type
        existing.is_active = consent_type == "opt_in"
        existing.source = source
        existing.consented_at = datetime.now(timezone.utc)
        existing.updated_at = datetime.now(timezone.utc)
        logger.info("consent_updated", phone=phone_number[-4:], type=consent_type, source=source)
        return existing

    consent = SMSConsent(
        phone_number=phone_number,
        consent_type=consent_type,
        source=source,
        is_active=consent_type == "opt_in",
        consented_at=datetime.now(timezone.utc),
    )
    session.add(consent)
    logger.info("consent_recorded", phone=phone_number[-4:], type=consent_type, source=source)
    return consent


async def process_opt_out(session: AsyncSession, phone_number: str) -> SMSConsent:
    """Record an opt-out for a phone number, preventing future SMS sends.

    This is a convenience wrapper around ``record_consent`` with
    ``consent_type="opt_out"``.
    """
    return await record_consent(session, phone_number, consent_type="opt_out", source="sms_keyword")


async def can_send_sms(
    session: AsyncSession,
    phone_number: str,
    timezone_str: str | None = None,
) -> tuple[bool, str | None]:
    """Combined gate: consent + quiet-hours check.

    Returns:
        (allowed, reason) — allowed is True when sending is permitted,
        otherwise reason explains why it was blocked.
    """
    # 1. Consent check
    has_consent = await check_consent(session, phone_number)
    if not has_consent:
        return False, "no_consent"

    # 2. Quiet-hours check
    if check_quiet_hours(timezone_str):
        return False, "quiet_hours"

    return True, None
