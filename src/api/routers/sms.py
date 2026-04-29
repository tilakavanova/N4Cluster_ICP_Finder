"""SMS API endpoints (NIF-231, NIF-233, NIF-234).

POST /sms/send          — send a single SMS
POST /sms/send-bulk     — send SMS to multiple campaign targets
POST /sms/callback      — Plivo delivery status webhook (no auth)
POST /sms/consent       — record SMS opt-in / opt-out consent
GET  /sms/consent/{phone_number} — check consent status
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.session import get_session
from src.services.sms import SMSService, handle_delivery_callback
from src.services.tcpa import can_send_sms, check_consent, record_consent, process_opt_out
from src.utils.logging import get_logger

logger = get_logger("sms_api")

router = APIRouter(
    prefix="/sms",
    tags=["sms"],
)


# ── Pydantic schemas ────────────────────────────────────────────────


class SMSSendRequest(BaseModel):
    to_number: str = Field(description="E.164 phone number, e.g. +14155551234")
    message: str = Field(min_length=1, max_length=1600)
    lead_id: UUID
    campaign_id: UUID
    target_id: UUID | None = None
    timezone: str | None = None


class SMSSendResponse(BaseModel):
    status: str
    message_uuid: str | None = None
    error: str | None = None


class SMSBulkSendRequest(BaseModel):
    campaign_id: UUID
    message_template: str = Field(min_length=1, max_length=1600)
    targets: list[dict] = Field(
        description="Each dict must have: target_id, lead_id, phone_number; optional: timezone, personalization_data"
    )


class SMSBulkSendResponse(BaseModel):
    sent: int
    failed: int
    blocked: int
    rate_limited: int
    details: list[dict]


class ConsentRequest(BaseModel):
    phone_number: str = Field(description="E.164 phone number")
    consent_type: str = Field(default="opt_in", pattern="^(opt_in|opt_out)$")
    source: str = Field(default="api")


class ConsentResponse(BaseModel):
    phone_number: str
    consent_type: str
    is_active: bool
    source: str | None


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/send", response_model=SMSSendResponse, dependencies=[Depends(require_auth)])
async def send_sms(
    body: SMSSendRequest,
    session: AsyncSession = Depends(get_session),
):
    """Send a single SMS with TCPA compliance and click tracking."""
    svc = SMSService()
    result = await svc.send_sms(
        session=session,
        to_number=body.to_number,
        message=body.message,
        lead_id=body.lead_id,
        campaign_id=body.campaign_id,
        target_id=body.target_id,
        timezone_str=body.timezone,
    )
    return SMSSendResponse(**result)


@router.post("/send-bulk", response_model=SMSBulkSendResponse, dependencies=[Depends(require_auth)])
async def send_bulk_sms(
    body: SMSBulkSendRequest,
    session: AsyncSession = Depends(get_session),
):
    """Send SMS to multiple targets from a campaign."""
    svc = SMSService()
    result = await svc.send_bulk_sms(
        session=session,
        campaign_id=body.campaign_id,
        message_template=body.message_template,
        targets=body.targets,
    )
    return SMSBulkSendResponse(**result)


@router.post("/callback")
async def delivery_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Plivo delivery status webhook — no auth required.

    Plivo sends delivery receipts as form-encoded POST data.
    """
    # Plivo can send form-encoded or JSON
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    logger.info("sms_delivery_callback", data=data)
    await handle_delivery_callback(session, data)
    return {"status": "ok"}


@router.post("/consent", response_model=ConsentResponse, dependencies=[Depends(require_auth)])
async def record_sms_consent(
    body: ConsentRequest,
    session: AsyncSession = Depends(get_session),
):
    """Record SMS opt-in or opt-out consent for a phone number."""
    if body.consent_type == "opt_out":
        consent = await process_opt_out(session, body.phone_number)
    else:
        consent = await record_consent(
            session,
            phone_number=body.phone_number,
            consent_type=body.consent_type,
            source=body.source,
        )
    await session.commit()
    return ConsentResponse(
        phone_number=consent.phone_number,
        consent_type=consent.consent_type,
        is_active=consent.is_active,
        source=consent.source,
    )


@router.get("/consent/{phone_number}", response_model=ConsentResponse, dependencies=[Depends(require_auth)])
async def get_consent_status(
    phone_number: str,
    session: AsyncSession = Depends(get_session),
):
    """Check SMS consent status for a phone number."""
    has_consent = await check_consent(session, phone_number)
    if has_consent:
        return ConsentResponse(
            phone_number=phone_number,
            consent_type="opt_in",
            is_active=True,
            source=None,
        )
    return ConsentResponse(
        phone_number=phone_number,
        consent_type="opt_out",
        is_active=False,
        source=None,
    )
