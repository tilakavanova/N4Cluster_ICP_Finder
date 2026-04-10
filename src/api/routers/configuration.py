"""Configuration Control Center API (NIF-137 through NIF-141)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_api_key
from src.db.session import get_session
from src.services.configuration import (
    get_config,
    set_config,
    validate_config,
    list_configs,
    get_config_history,
    create_override,
)
from src.utils.logging import get_logger

logger = get_logger("configuration_api")

router = APIRouter(
    prefix="/config",
    tags=["configuration"],
    dependencies=[Depends(require_api_key)],
)


# ── Pydantic schemas ───────────────────────────────────────────


class ConfigSetBody(BaseModel):
    value: object
    data_type: str | None = Field(None, pattern="^(string|int|float|bool|json)$")
    description: str | None = None
    is_secret: bool = False
    changed_by: str = "system"


class ConfigOverrideCreate(BaseModel):
    config_id: UUID
    scope_type: str = Field(pattern="^(market|cuisine|region)$")
    scope_value: str
    override_value: object
    priority: int = 0


class ConfigValidateBody(BaseModel):
    namespace: str
    key: str
    value: object
    data_type: str = Field(pattern="^(string|int|float|bool|json)$")


# ── Endpoints ──────────────────────────────────────────────────


@router.get("")
async def list_config_entries(
    namespace: str | None = Query(None, description="Filter by namespace"),
    session: AsyncSession = Depends(get_session),
):
    """List configuration entries, optionally filtered by namespace (NIF-137)."""
    return await list_configs(session, namespace)


@router.get("/{namespace}/{key}")
async def get_config_value(
    namespace: str,
    key: str,
    scope_type: str | None = Query(None, description="Override scope type (market, cuisine, region)"),
    scope_value: str | None = Query(None, description="Override scope value"),
    session: AsyncSession = Depends(get_session),
):
    """Get effective configuration value with optional scope override (NIF-137, NIF-139)."""
    scope = None
    if scope_type and scope_value:
        scope = {"scope_type": scope_type, "scope_value": scope_value}

    result = await get_config(session, namespace, key, scope=scope)
    if not result:
        raise HTTPException(404, f"Config '{namespace}/{key}' not found")
    return result


@router.put("/{namespace}/{key}")
async def set_config_value(
    namespace: str,
    key: str,
    body: ConfigSetBody,
    session: AsyncSession = Depends(get_session),
):
    """Set a configuration value with version tracking (NIF-137, NIF-138)."""
    try:
        result = await set_config(
            session,
            namespace=namespace,
            key=key,
            value=body.value,
            changed_by=body.changed_by,
            data_type=body.data_type,
            description=body.description,
            is_secret=body.is_secret,
        )
        await session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/overrides")
async def create_config_override(
    body: ConfigOverrideCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a scope-specific configuration override (NIF-139)."""
    try:
        result = await create_override(
            session,
            config_id=body.config_id,
            scope_type=body.scope_type,
            scope_value=body.scope_value,
            override_value=body.override_value,
            priority=body.priority,
        )
        await session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/{config_id}/history")
async def get_version_history(
    config_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get version history for a configuration entry (NIF-138)."""
    history = await get_config_history(session, config_id)
    return history


@router.post("/validate")
async def validate_config_value(body: ConfigValidateBody):
    """Validate a configuration value without persisting (NIF-140)."""
    is_valid, error_msg = validate_config(
        namespace=body.namespace,
        key=body.key,
        value=body.value,
        data_type=body.data_type,
    )
    return {"valid": is_valid, "error": error_msg if not is_valid else None}
