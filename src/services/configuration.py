"""Configuration Control Center service (NIF-137 through NIF-141)."""

from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ConfigRegistry, ConfigVersion, ConfigOverride
from src.utils.logging import get_logger

logger = get_logger("configuration")

VALID_DATA_TYPES = {"string", "int", "float", "bool", "json"}


def validate_config(namespace: str, key: str, value, data_type: str) -> tuple[bool, str]:
    """Validate a configuration value against its declared data_type (NIF-140).

    Returns (is_valid, error_message).
    """
    if data_type not in VALID_DATA_TYPES:
        return False, f"Invalid data_type '{data_type}'. Must be one of: {', '.join(sorted(VALID_DATA_TYPES))}"

    if not namespace or not namespace.strip():
        return False, "Namespace must not be empty"

    if not key or not key.strip():
        return False, "Key must not be empty"

    if data_type == "string":
        if not isinstance(value, str):
            return False, f"Expected string, got {type(value).__name__}"
    elif data_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return False, f"Expected int, got {type(value).__name__}"
    elif data_type == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False, f"Expected float, got {type(value).__name__}"
    elif data_type == "bool":
        if not isinstance(value, bool):
            return False, f"Expected bool, got {type(value).__name__}"
    elif data_type == "json":
        if not isinstance(value, (dict, list)):
            return False, f"Expected dict or list for json type, got {type(value).__name__}"

    return True, ""


async def get_config(
    session: AsyncSession,
    namespace: str,
    key: str,
    scope: dict | None = None,
) -> dict | None:
    """Get effective configuration value, considering overrides (NIF-137, NIF-139).

    If scope is provided (e.g. {"scope_type": "market", "scope_value": "NYC"}),
    returns the override value if an active override matches, otherwise the base value.
    """
    result = await session.execute(
        select(ConfigRegistry).where(
            ConfigRegistry.namespace == namespace,
            ConfigRegistry.key == key,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        return None

    effective_value = config.value

    # Check for scope-specific override
    if scope and scope.get("scope_type") and scope.get("scope_value"):
        override_result = await session.execute(
            select(ConfigOverride)
            .where(
                ConfigOverride.config_id == config.id,
                ConfigOverride.scope_type == scope["scope_type"],
                ConfigOverride.scope_value == scope["scope_value"],
                ConfigOverride.is_active == True,  # noqa: E712
            )
            .order_by(ConfigOverride.priority.desc())
            .limit(1)
        )
        override = override_result.scalar_one_or_none()
        if override:
            effective_value = override.override_value

    return {
        "id": str(config.id),
        "namespace": config.namespace,
        "key": config.key,
        "value": effective_value,
        "data_type": config.data_type,
        "is_secret": config.is_secret,
        "description": config.description,
        "is_override": effective_value is not config.value,
    }


async def set_config(
    session: AsyncSession,
    namespace: str,
    key: str,
    value,
    changed_by: str = "system",
    data_type: str | None = None,
    description: str | None = None,
    is_secret: bool = False,
) -> dict:
    """Set a configuration value with version tracking (NIF-137, NIF-138).

    Creates the config entry if it doesn't exist, otherwise updates it.
    """
    result = await session.execute(
        select(ConfigRegistry).where(
            ConfigRegistry.namespace == namespace,
            ConfigRegistry.key == key,
        )
    )
    config = result.scalar_one_or_none()

    resolved_data_type = data_type or (config.data_type if config else "string")

    # Validate
    is_valid, error_msg = validate_config(namespace, key, value, resolved_data_type)
    if not is_valid:
        raise ValueError(error_msg)

    # Wrap value in JSONB-friendly format
    jsonb_value = value if isinstance(value, (dict, list)) else {"_value": value}

    if config:
        old_value = config.value
        # Determine next version number
        version_count = await session.execute(
            select(func.count(ConfigVersion.id)).where(
                ConfigVersion.config_id == config.id
            )
        )
        next_version = (version_count.scalar() or 0) + 1

        # Record version
        version = ConfigVersion(
            config_id=config.id,
            version_number=next_version,
            old_value=old_value,
            new_value=jsonb_value,
            changed_by=changed_by,
        )
        session.add(version)

        config.value = jsonb_value
        if description is not None:
            config.description = description
    else:
        config = ConfigRegistry(
            namespace=namespace,
            key=key,
            value=jsonb_value,
            data_type=resolved_data_type,
            description=description,
            is_secret=is_secret,
        )
        session.add(config)
        await session.flush()

        # Record initial version
        version = ConfigVersion(
            config_id=config.id,
            version_number=1,
            old_value=None,
            new_value=jsonb_value,
            changed_by=changed_by,
        )
        session.add(version)

    await session.flush()
    logger.info("config_set", namespace=namespace, key=key, changed_by=changed_by)

    return {
        "id": str(config.id),
        "namespace": config.namespace,
        "key": config.key,
        "value": config.value,
        "data_type": config.data_type,
        "is_secret": config.is_secret,
        "description": config.description,
    }


async def list_configs(
    session: AsyncSession,
    namespace: str | None = None,
) -> list[dict]:
    """List configuration entries, optionally filtered by namespace (NIF-137)."""
    query = select(ConfigRegistry).order_by(ConfigRegistry.namespace, ConfigRegistry.key)
    if namespace:
        query = query.where(ConfigRegistry.namespace == namespace)

    result = await session.execute(query)
    configs = result.scalars().all()

    return [
        {
            "id": str(c.id),
            "namespace": c.namespace,
            "key": c.key,
            "value": c.value if not c.is_secret else "***",
            "data_type": c.data_type,
            "is_secret": c.is_secret,
            "description": c.description,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in configs
    ]


async def get_config_history(
    session: AsyncSession,
    config_id: UUID,
) -> list[dict]:
    """Get version history for a configuration entry (NIF-138)."""
    result = await session.execute(
        select(ConfigVersion)
        .where(ConfigVersion.config_id == config_id)
        .order_by(ConfigVersion.version_number.desc())
    )
    versions = result.scalars().all()

    return [
        {
            "id": str(v.id),
            "config_id": str(v.config_id),
            "version_number": v.version_number,
            "old_value": v.old_value,
            "new_value": v.new_value,
            "changed_by": v.changed_by,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]


async def create_override(
    session: AsyncSession,
    config_id: UUID,
    scope_type: str,
    scope_value: str,
    override_value,
    priority: int = 0,
) -> dict:
    """Create a scope-specific configuration override (NIF-139)."""
    config = await session.get(ConfigRegistry, config_id)
    if not config:
        raise ValueError(f"Config entry {config_id} not found")

    valid_scope_types = {"market", "cuisine", "region"}
    if scope_type not in valid_scope_types:
        raise ValueError(f"Invalid scope_type '{scope_type}'. Must be one of: {', '.join(sorted(valid_scope_types))}")

    jsonb_value = override_value if isinstance(override_value, (dict, list)) else {"_value": override_value}

    override = ConfigOverride(
        config_id=config_id,
        scope_type=scope_type,
        scope_value=scope_value,
        override_value=jsonb_value,
        priority=priority,
        is_active=True,
    )
    session.add(override)
    await session.flush()
    logger.info("config_override_created", config_id=str(config_id), scope_type=scope_type, scope_value=scope_value)

    return {
        "id": str(override.id),
        "config_id": str(override.config_id),
        "scope_type": override.scope_type,
        "scope_value": override.scope_value,
        "override_value": override.override_value,
        "priority": override.priority,
        "is_active": override.is_active,
    }
