"""LLM Prompt Versioning Service (NIF-264)."""

from datetime import datetime, timezone

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LLMPromptTemplate
from src.utils.logging import get_logger

logger = get_logger("prompt_versioning")


async def get_active_prompt(
    session: AsyncSession,
    name: str,
) -> LLMPromptTemplate | None:
    """Return the active version of a named prompt."""
    result = await session.execute(
        select(LLMPromptTemplate).where(
            and_(
                LLMPromptTemplate.name == name,
                LLMPromptTemplate.is_active == True,  # noqa: E712
            )
        )
    )
    return result.scalar_one_or_none()


async def create_prompt(
    session: AsyncSession,
    name: str,
    prompt_text: str,
    model_id: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 4000,
    metadata: dict | None = None,
    created_by: str = "system",
) -> LLMPromptTemplate:
    """Create a new prompt template (version 1)."""
    # Check if a prompt with this name already exists
    existing = await session.execute(
        select(LLMPromptTemplate).where(LLMPromptTemplate.name == name).limit(1)
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Prompt '{name}' already exists. Use update_prompt to create a new version.")

    template = LLMPromptTemplate(
        name=name,
        version=1,
        prompt_text=prompt_text,
        model_id=model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        metadata_=metadata or {},
        is_active=True,
        created_by=created_by,
    )
    session.add(template)
    await session.flush()

    logger.info("prompt_created", name=name, version=1, model_id=model_id)
    return template


async def update_prompt(
    session: AsyncSession,
    name: str,
    prompt_text: str | None = None,
    model_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    metadata: dict | None = None,
    created_by: str = "system",
) -> LLMPromptTemplate:
    """Create a new version of an existing prompt, deactivating the old one."""
    # Get current active version
    current = await get_active_prompt(session, name)
    if not current:
        raise ValueError(f"No active prompt found with name '{name}'")

    new_version = current.version + 1

    # Deactivate old version
    current.is_active = False
    current.updated_at = datetime.now(timezone.utc)

    # Create new version
    template = LLMPromptTemplate(
        name=name,
        version=new_version,
        prompt_text=prompt_text if prompt_text is not None else current.prompt_text,
        model_id=model_id if model_id is not None else current.model_id,
        temperature=temperature if temperature is not None else current.temperature,
        max_tokens=max_tokens if max_tokens is not None else current.max_tokens,
        metadata_=metadata if metadata is not None else (current.metadata_ or {}),
        is_active=True,
        created_by=created_by,
    )
    session.add(template)
    await session.flush()

    logger.info("prompt_updated", name=name, version=new_version)
    return template


async def list_prompts(
    session: AsyncSession,
) -> list[LLMPromptTemplate]:
    """List all active prompts."""
    result = await session.execute(
        select(LLMPromptTemplate)
        .where(LLMPromptTemplate.is_active == True)  # noqa: E712
        .order_by(LLMPromptTemplate.name)
    )
    return list(result.scalars().all())


async def get_prompt_history(
    session: AsyncSession,
    name: str,
) -> list[LLMPromptTemplate]:
    """Get all versions of a prompt, newest first."""
    result = await session.execute(
        select(LLMPromptTemplate)
        .where(LLMPromptTemplate.name == name)
        .order_by(LLMPromptTemplate.version.desc())
    )
    return list(result.scalars().all())


async def rollback_prompt(
    session: AsyncSession,
    name: str,
    version: int,
) -> LLMPromptTemplate:
    """Reactivate an older version of a prompt, deactivating the current active one."""
    # Find the target version
    result = await session.execute(
        select(LLMPromptTemplate).where(
            and_(
                LLMPromptTemplate.name == name,
                LLMPromptTemplate.version == version,
            )
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise ValueError(f"Prompt '{name}' version {version} not found")

    # Deactivate all versions of this prompt
    all_result = await session.execute(
        select(LLMPromptTemplate).where(LLMPromptTemplate.name == name)
    )
    for prompt in all_result.scalars().all():
        prompt.is_active = False
        prompt.updated_at = datetime.now(timezone.utc)

    # Reactivate target version
    target.is_active = True
    target.updated_at = datetime.now(timezone.utc)
    await session.flush()

    logger.info("prompt_rolled_back", name=name, version=version)
    return target
