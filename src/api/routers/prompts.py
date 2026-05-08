"""LLM Prompt Versioning API (NIF-264)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_auth
from src.db.session import get_session
from src.services.prompt_versioning import (
    get_active_prompt,
    create_prompt,
    update_prompt,
    list_prompts,
    get_prompt_history,
    rollback_prompt,
)
from src.utils.logging import get_logger

logger = get_logger("prompts_api")

router = APIRouter(
    prefix="/prompts",
    tags=["prompts"],
    dependencies=[Depends(require_auth)],
)


# -- Schemas -------------------------------------------------------------------

class CreatePromptBody(BaseModel):
    name: str
    prompt_text: str
    model_id: str = "gpt-4o-mini"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4000, ge=1, le=128000)
    metadata: dict | None = None
    created_by: str = "system"


class UpdatePromptBody(BaseModel):
    prompt_text: str | None = None
    model_id: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=128000)
    metadata: dict | None = None
    created_by: str = "system"


class RollbackBody(BaseModel):
    version: int


# -- Helpers -------------------------------------------------------------------

def _template_to_dict(t) -> dict:
    return {
        "id": str(t.id),
        "name": t.name,
        "version": t.version,
        "prompt_text": t.prompt_text,
        "model_id": t.model_id,
        "temperature": t.temperature,
        "max_tokens": t.max_tokens,
        "metadata": t.metadata_,
        "is_active": t.is_active,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


# -- Endpoints -----------------------------------------------------------------

@router.get("")
async def list_active_prompts(
    session: AsyncSession = Depends(get_session),
):
    """List all active prompt templates (NIF-264)."""
    templates = await list_prompts(session)
    return [_template_to_dict(t) for t in templates]


@router.get("/{name}")
async def get_prompt_by_name(
    name: str,
    session: AsyncSession = Depends(get_session),
):
    """Get the active version of a prompt by name (NIF-264)."""
    template = await get_active_prompt(session, name)
    if not template:
        raise HTTPException(404, f"No active prompt found with name '{name}'")
    return _template_to_dict(template)


@router.post("")
async def create_new_prompt(
    body: CreatePromptBody,
    session: AsyncSession = Depends(get_session),
):
    """Create a new prompt template (NIF-264)."""
    try:
        template = await create_prompt(
            session,
            name=body.name,
            prompt_text=body.prompt_text,
            model_id=body.model_id,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            metadata=body.metadata,
            created_by=body.created_by,
        )
        await session.commit()
        return _template_to_dict(template)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.patch("/{name}")
async def update_existing_prompt(
    name: str,
    body: UpdatePromptBody,
    session: AsyncSession = Depends(get_session),
):
    """Update a prompt template (creates a new version) (NIF-264)."""
    try:
        template = await update_prompt(
            session,
            name=name,
            prompt_text=body.prompt_text,
            model_id=body.model_id,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            metadata=body.metadata,
            created_by=body.created_by,
        )
        await session.commit()
        return _template_to_dict(template)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{name}/history")
async def get_prompt_version_history(
    name: str,
    session: AsyncSession = Depends(get_session),
):
    """Get all versions of a prompt (NIF-264)."""
    versions = await get_prompt_history(session, name)
    if not versions:
        raise HTTPException(404, f"No prompt found with name '{name}'")
    return [_template_to_dict(t) for t in versions]


@router.post("/{name}/rollback")
async def rollback_prompt_version(
    name: str,
    body: RollbackBody,
    session: AsyncSession = Depends(get_session),
):
    """Rollback a prompt to a specific version (NIF-264)."""
    try:
        template = await rollback_prompt(session, name, body.version)
        await session.commit()
        return _template_to_dict(template)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
