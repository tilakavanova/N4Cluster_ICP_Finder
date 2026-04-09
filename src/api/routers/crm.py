"""CRM core API endpoints — accounts, contacts, lead lifecycle."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.auth import require_api_key
from src.db.models import (
    Account, Contact, Lead,
    LeadStageHistory, LeadAssignmentHistory,
)
from src.db.session import get_session
from src.utils.logging import get_logger

logger = get_logger("crm")

router = APIRouter(prefix="/crm", tags=["crm"], dependencies=[Depends(require_api_key)])


# --- Schemas ---

class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    business_type: str | None = None
    location_count: int = 1
    website: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = Field(None, max_length=2)
    zip_code: str | None = Field(None, max_length=10)
    restaurant_id: UUID | None = None
    notes: str | None = None


class ContactCreate(BaseModel):
    account_id: UUID
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = None
    role: str | None = Field(None, max_length=50)
    is_primary: bool = False


class LeadStageUpdate(BaseModel):
    lifecycle_stage: str = Field(..., max_length=30)
    changed_by: str = "system"


class LeadOwnerUpdate(BaseModel):
    owner: str = Field(..., min_length=1, max_length=100)
    changed_by: str = "system"


class LeadLinkUpdate(BaseModel):
    account_id: UUID | None = None
    contact_id: UUID | None = None


# --- Account endpoints ---

@router.post("/accounts", status_code=201)
async def create_account(payload: AccountCreate, session: AsyncSession = Depends(get_session)):
    """Create a new account (merchant business)."""
    account = Account(**payload.model_dump())
    session.add(account)
    await session.flush()
    logger.info("account_created", account_id=str(account.id), name=payload.name)
    return {"id": str(account.id), "name": account.name}


@router.get("/accounts")
async def list_accounts(
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List accounts with optional search."""
    query = select(Account).order_by(Account.created_at.desc())
    if search:
        query = query.where(Account.name.ilike(f"%{search}%"))
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(query)
    accounts = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "business_type": a.business_type,
            "location_count": a.location_count,
            "city": a.city,
            "state": a.state,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in accounts
    ]


@router.get("/accounts/{account_id}")
async def get_account(account_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get account details with contacts."""
    result = await session.execute(
        select(Account).options(selectinload(Account.contacts)).where(Account.id == account_id)
    )
    account = result.unique().scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    return {
        "id": str(account.id),
        "name": account.name,
        "business_type": account.business_type,
        "location_count": account.location_count,
        "website": account.website,
        "phone": account.phone,
        "city": account.city,
        "state": account.state,
        "zip_code": account.zip_code,
        "notes": account.notes,
        "contacts": [
            {
                "id": str(c.id),
                "first_name": c.first_name,
                "last_name": c.last_name,
                "email": c.email,
                "role": c.role,
                "is_primary": c.is_primary,
            }
            for c in account.contacts
        ],
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }


# --- Contact endpoints ---

@router.post("/contacts", status_code=201)
async def create_contact(payload: ContactCreate, session: AsyncSession = Depends(get_session)):
    """Create a contact linked to an account."""
    account = await session.get(Account, payload.account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    contact = Contact(**payload.model_dump())
    session.add(contact)
    await session.flush()
    logger.info("contact_created", contact_id=str(contact.id), account_id=str(payload.account_id))
    return {"id": str(contact.id), "name": f"{contact.first_name} {contact.last_name}"}


@router.get("/contacts")
async def list_contacts(
    account_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """List contacts, optionally filtered by account."""
    query = select(Contact).order_by(Contact.created_at.desc())
    if account_id:
        query = query.where(Contact.account_id == account_id)
    query = query.limit(100)
    result = await session.execute(query)
    contacts = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "account_id": str(c.account_id),
            "first_name": c.first_name,
            "last_name": c.last_name,
            "email": c.email,
            "phone": c.phone,
            "role": c.role,
            "is_primary": c.is_primary,
        }
        for c in contacts
    ]


# --- Lead lifecycle endpoints ---

@router.patch("/leads/{lead_id}/stage")
async def update_lead_stage(
    lead_id: UUID, payload: LeadStageUpdate, session: AsyncSession = Depends(get_session),
):
    """Update lead lifecycle stage with history tracking."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    old_stage = lead.lifecycle_stage
    lead.lifecycle_stage = payload.lifecycle_stage

    session.add(LeadStageHistory(
        lead_id=lead.id,
        from_stage=old_stage,
        to_stage=payload.lifecycle_stage,
        changed_by=payload.changed_by,
    ))

    logger.info("lead_stage_changed", lead_id=str(lead_id), from_stage=old_stage, to_stage=payload.lifecycle_stage)
    return {"lead_id": str(lead_id), "lifecycle_stage": payload.lifecycle_stage}


@router.patch("/leads/{lead_id}/owner")
async def update_lead_owner(
    lead_id: UUID, payload: LeadOwnerUpdate, session: AsyncSession = Depends(get_session),
):
    """Assign lead to owner with history tracking."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    old_owner = lead.owner
    lead.owner = payload.owner

    session.add(LeadAssignmentHistory(
        lead_id=lead.id,
        from_owner=old_owner,
        to_owner=payload.owner,
        changed_by=payload.changed_by,
    ))

    logger.info("lead_owner_changed", lead_id=str(lead_id), from_owner=old_owner, to_owner=payload.owner)
    return {"lead_id": str(lead_id), "owner": payload.owner}


@router.patch("/leads/{lead_id}/link")
async def link_lead(
    lead_id: UUID, payload: LeadLinkUpdate, session: AsyncSession = Depends(get_session),
):
    """Link lead to account and/or contact."""
    lead = await session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    if payload.account_id is not None:
        lead.account_id = payload.account_id
    if payload.contact_id is not None:
        lead.contact_id = payload.contact_id

    logger.info("lead_linked", lead_id=str(lead_id), account_id=str(payload.account_id), contact_id=str(payload.contact_id))
    return {"lead_id": str(lead_id), "account_id": str(lead.account_id) if lead.account_id else None}


@router.get("/leads/{lead_id}/history")
async def lead_history(lead_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get combined stage + assignment history for a lead."""
    stages = (await session.execute(
        select(LeadStageHistory)
        .where(LeadStageHistory.lead_id == lead_id)
        .order_by(LeadStageHistory.changed_at.desc())
    )).scalars().all()

    assignments = (await session.execute(
        select(LeadAssignmentHistory)
        .where(LeadAssignmentHistory.lead_id == lead_id)
        .order_by(LeadAssignmentHistory.changed_at.desc())
    )).scalars().all()

    return {
        "lead_id": str(lead_id),
        "stage_history": [
            {
                "from_stage": s.from_stage,
                "to_stage": s.to_stage,
                "changed_by": s.changed_by,
                "changed_at": s.changed_at.isoformat() if s.changed_at else None,
            }
            for s in stages
        ],
        "assignment_history": [
            {
                "from_owner": a.from_owner,
                "to_owner": a.to_owner,
                "changed_by": a.changed_by,
                "changed_at": a.changed_at.isoformat() if a.changed_at else None,
            }
            for a in assignments
        ],
    }
