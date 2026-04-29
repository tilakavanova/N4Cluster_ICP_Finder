"""GDPR compliance and data retention API (NIF-241).

Endpoints:
  POST   /compliance/data-export/{lead_id}   — export all PII for a lead (right of access)
  DELETE /compliance/data-erase/{lead_id}    — hard-delete PII, anonymize audit trail (right to erasure)
  POST   /compliance/consent/{lead_id}       — record consent with timestamp and scope
  GET    /compliance/consent/{lead_id}       — get consent status
  POST   /compliance/retention-cleanup       — trigger data retention cleanup
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth, require_scope
from src.db.session import get_session
from src.services.compliance import (
    export_lead_data,
    erase_lead_data,
    record_consent,
    get_consent_status,
    cleanup_expired_data,
    DEFAULT_DATA_RETENTION_DAYS,
)
from src.utils.logging import get_logger

logger = get_logger("compliance_api")

router = APIRouter(
    prefix="/compliance",
    tags=["compliance"],
    dependencies=[Depends(require_auth)],
)


# -- Pydantic schemas ---------------------------------------------------------

class ConsentRequest(BaseModel):
    scope: str = Field(..., description="Consent scope, e.g. 'marketing_email', 'data_processing', 'analytics'")
    granted: bool = Field(..., description="Whether consent is granted or withdrawn")


class RetentionCleanupRequest(BaseModel):
    retention_days: int = Field(
        default=DEFAULT_DATA_RETENTION_DAYS,
        ge=30,
        le=3650,
        description="Number of days to retain data",
    )


# -- Endpoints ----------------------------------------------------------------

@router.post("/data-export/{lead_id}")
async def data_export(
    lead_id: UUID,
    auth: dict = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Export all PII and related data for a lead (GDPR right of access)."""
    export = await export_lead_data(session, lead_id)
    if not export:
        raise HTTPException(status_code=404, detail="Lead not found")
    return export


@router.delete("/data-erase/{lead_id}")
async def data_erase(
    lead_id: UUID,
    auth: dict = Depends(require_scope("admin:all")),
    session: AsyncSession = Depends(get_session),
):
    """Hard-delete PII, anonymize audit trail, remove from HubSpot (GDPR right to erasure).

    Requires scope: admin:all
    """
    result = await erase_lead_data(
        session,
        lead_id,
        performed_by=auth.get("sub", "unknown"),
    )
    if result.get("error") == "lead_not_found":
        raise HTTPException(status_code=404, detail="Lead not found")
    return result


@router.post("/consent/{lead_id}")
async def post_consent(
    lead_id: UUID,
    body: ConsentRequest,
    auth: dict = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Record consent with timestamp and scope."""
    result = await record_consent(
        session,
        lead_id,
        scope=body.scope,
        granted=body.granted,
        recorded_by=auth.get("sub", "system"),
    )
    if result.get("error") == "lead_not_found":
        raise HTTPException(status_code=404, detail="Lead not found")
    return result


@router.get("/consent/{lead_id}")
async def get_consent(
    lead_id: UUID,
    auth: dict = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    """Get consent status for a lead."""
    return await get_consent_status(session, lead_id)


@router.post("/retention-cleanup", dependencies=[Depends(require_scope("admin:all"))])
async def retention_cleanup(
    body: RetentionCleanupRequest = RetentionCleanupRequest(),
    auth: dict = Depends(require_scope("admin:all")),
    session: AsyncSession = Depends(get_session),
):
    """Trigger data retention cleanup. Requires scope: admin:all."""
    return await cleanup_expired_data(session, retention_days=body.retention_days)
