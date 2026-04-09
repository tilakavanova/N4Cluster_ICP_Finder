"""CRM core API endpoints — accounts, contacts, lead lifecycle, tasks, merge."""

from datetime import datetime, timezone
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
    AccountHistory, ContactHistory, FollowUpTask,
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


class AccountUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    business_type: str | None = None
    location_count: int | None = None
    website: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = Field(None, max_length=2)
    zip_code: str | None = Field(None, max_length=10)
    notes: str | None = None
    changed_by: str = "system"


class ContactUpdate(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = None
    role: str | None = Field(None, max_length=50)
    is_primary: bool | None = None
    changed_by: str = "system"


class TaskCreate(BaseModel):
    lead_id: UUID
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    task_type: str = Field("follow_up", pattern="^(follow_up|call|email|demo|other)$")
    priority: str = Field("medium", pattern="^(low|medium|high|urgent)$")
    assigned_to: str | None = None
    due_date: datetime | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = None
    priority: str | None = Field(None, pattern="^(low|medium|high|urgent)$")
    status: str | None = Field(None, pattern="^(pending|in_progress|completed|cancelled)$")
    assigned_to: str | None = None
    due_date: datetime | None = None


class LeadMergeRequest(BaseModel):
    source_lead_id: UUID
    target_lead_id: UUID
    merged_by: str = "system"


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


# --- Account update with history (NIF-70) ---

ACCOUNT_TRACKED_FIELDS = {"name", "business_type", "location_count", "website", "phone", "city", "state", "zip_code", "notes"}


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: UUID, payload: AccountUpdate, session: AsyncSession = Depends(get_session),
):
    """Update account fields with change history tracking."""
    account = await session.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    changes = []
    update_data = payload.model_dump(exclude_unset=True, exclude={"changed_by"})
    for field, new_value in update_data.items():
        if field not in ACCOUNT_TRACKED_FIELDS:
            continue
        old_value = getattr(account, field)
        str_old = str(old_value) if old_value is not None else None
        str_new = str(new_value) if new_value is not None else None
        if str_old != str_new:
            setattr(account, field, new_value)
            session.add(AccountHistory(
                account_id=account.id,
                field_name=field,
                old_value=str_old,
                new_value=str_new,
                changed_by=payload.changed_by,
            ))
            changes.append(field)

    logger.info("account_updated", account_id=str(account_id), changed_fields=changes)
    return {"id": str(account_id), "updated_fields": changes}


@router.get("/accounts/{account_id}/history")
async def account_history(account_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get field change history for an account."""
    result = await session.execute(
        select(AccountHistory)
        .where(AccountHistory.account_id == account_id)
        .order_by(AccountHistory.changed_at.desc())
        .limit(100)
    )
    entries = result.scalars().all()
    return [
        {
            "field_name": e.field_name,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "changed_by": e.changed_by,
            "changed_at": e.changed_at.isoformat() if e.changed_at else None,
        }
        for e in entries
    ]


# --- Contact update with history (NIF-71) ---

CONTACT_TRACKED_FIELDS = {"first_name", "last_name", "email", "phone", "role", "is_primary"}


@router.patch("/contacts/{contact_id}")
async def update_contact(
    contact_id: UUID, payload: ContactUpdate, session: AsyncSession = Depends(get_session),
):
    """Update contact fields with change history tracking."""
    contact = await session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")

    changes = []
    update_data = payload.model_dump(exclude_unset=True, exclude={"changed_by"})
    for field, new_value in update_data.items():
        if field not in CONTACT_TRACKED_FIELDS:
            continue
        old_value = getattr(contact, field)
        str_old = str(old_value) if old_value is not None else None
        str_new = str(new_value) if new_value is not None else None
        if str_old != str_new:
            setattr(contact, field, new_value)
            session.add(ContactHistory(
                contact_id=contact.id,
                field_name=field,
                old_value=str_old,
                new_value=str_new,
                changed_by=payload.changed_by,
            ))
            changes.append(field)

    logger.info("contact_updated", contact_id=str(contact_id), changed_fields=changes)
    return {"id": str(contact_id), "updated_fields": changes}


@router.get("/contacts/{contact_id}/history")
async def contact_history(contact_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get field change history for a contact."""
    result = await session.execute(
        select(ContactHistory)
        .where(ContactHistory.contact_id == contact_id)
        .order_by(ContactHistory.changed_at.desc())
        .limit(100)
    )
    entries = result.scalars().all()
    return [
        {
            "field_name": e.field_name,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "changed_by": e.changed_by,
            "changed_at": e.changed_at.isoformat() if e.changed_at else None,
        }
        for e in entries
    ]


# --- Follow-up tasks (NIF-112) ---

@router.post("/tasks", status_code=201)
async def create_task(payload: TaskCreate, session: AsyncSession = Depends(get_session)):
    """Create a follow-up task for a lead."""
    lead = await session.get(Lead, payload.lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    task = FollowUpTask(**payload.model_dump())
    session.add(task)
    await session.flush()
    logger.info("task_created", task_id=str(task.id), lead_id=str(payload.lead_id), type=payload.task_type)
    return {
        "id": str(task.id),
        "lead_id": str(task.lead_id),
        "title": task.title,
        "status": task.status,
    }


@router.get("/tasks")
async def list_tasks(
    lead_id: UUID | None = Query(None),
    status: str | None = Query(None),
    assigned_to: str | None = Query(None),
    priority: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List follow-up tasks with optional filters."""
    query = select(FollowUpTask).order_by(FollowUpTask.due_date.asc().nullslast(), FollowUpTask.created_at.desc())
    if lead_id:
        query = query.where(FollowUpTask.lead_id == lead_id)
    if status:
        query = query.where(FollowUpTask.status == status)
    if assigned_to:
        query = query.where(FollowUpTask.assigned_to == assigned_to)
    if priority:
        query = query.where(FollowUpTask.priority == priority)
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(query)
    tasks = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "lead_id": str(t.lead_id),
            "title": t.title,
            "task_type": t.task_type,
            "priority": t.priority,
            "status": t.status,
            "assigned_to": t.assigned_to,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tasks
    ]


@router.patch("/tasks/{task_id}")
async def update_task(task_id: UUID, payload: TaskUpdate, session: AsyncSession = Depends(get_session)):
    """Update a follow-up task."""
    task = await session.get(FollowUpTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(task, field, value)

    if payload.status == "completed" and not task.completed_at:
        task.completed_at = datetime.now(timezone.utc)

    logger.info("task_updated", task_id=str(task_id), updates=list(update_data.keys()))
    return {"id": str(task_id), "status": task.status}


# --- Lead merge (NIF-115) ---

@router.post("/leads/merge")
async def merge_leads(payload: LeadMergeRequest, session: AsyncSession = Depends(get_session)):
    """Merge source lead into target lead. Source is marked as merged."""
    if payload.source_lead_id == payload.target_lead_id:
        raise HTTPException(400, "Cannot merge a lead into itself")

    source = await session.get(Lead, payload.source_lead_id)
    target = await session.get(Lead, payload.target_lead_id)

    if not source:
        raise HTTPException(404, "Source lead not found")
    if not target:
        raise HTTPException(404, "Target lead not found")
    if source.is_merged:
        raise HTTPException(400, "Source lead is already merged")

    # Transfer enrichment data from source if target lacks it
    merge_fields = [
        "company", "business_type", "locations", "interest", "message",
        "restaurant_id", "icp_score_id", "icp_fit_label", "icp_total_score",
        "matched_restaurant_name", "match_confidence", "is_independent",
        "has_delivery", "delivery_platforms", "has_pos", "pos_provider",
        "geo_density_score", "hubspot_contact_id", "hubspot_deal_id",
        "utm_source", "utm_medium", "utm_campaign", "account_id", "contact_id",
    ]
    merged_fields = []
    for field in merge_fields:
        target_val = getattr(target, field)
        source_val = getattr(source, field)
        if (target_val is None or target_val == [] or target_val == 0) and source_val:
            setattr(target, field, source_val)
            merged_fields.append(field)

    # Mark source as merged
    source.is_merged = True
    source.merged_into_id = target.id
    source.status = "merged"

    # Move source's stage/assignment history to target
    stage_entries = (await session.execute(
        select(LeadStageHistory).where(LeadStageHistory.lead_id == source.id)
    )).scalars().all()
    for entry in stage_entries:
        entry.lead_id = target.id

    assignment_entries = (await session.execute(
        select(LeadAssignmentHistory).where(LeadAssignmentHistory.lead_id == source.id)
    )).scalars().all()
    for entry in assignment_entries:
        entry.lead_id = target.id

    # Move follow-up tasks to target
    tasks = (await session.execute(
        select(FollowUpTask).where(FollowUpTask.lead_id == source.id)
    )).scalars().all()
    for task in tasks:
        task.lead_id = target.id

    logger.info(
        "leads_merged",
        source_id=str(source.id),
        target_id=str(target.id),
        merged_fields=merged_fields,
        merged_by=payload.merged_by,
    )
    return {
        "target_lead_id": str(target.id),
        "source_lead_id": str(source.id),
        "merged_fields": merged_fields,
        "source_status": "merged",
    }
