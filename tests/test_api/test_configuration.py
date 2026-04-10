"""Tests for Configuration Control Center (NIF-137 through NIF-141)."""

import uuid

import pytest

from src.db.models import ConfigRegistry, ConfigVersion, ConfigOverride


class TestConfigRegistry:
    """NIF-137: Configuration registry model."""

    def test_config_creation(self):
        config = ConfigRegistry(
            namespace="scoring",
            key="default_weight",
            value={"_value": 15},
            data_type="int",
            description="Default signal weight",
            is_secret=False,
        )
        assert config.namespace == "scoring"
        assert config.key == "default_weight"
        assert config.value == {"_value": 15}
        assert config.data_type == "int"
        assert config.is_secret is False

    def test_config_defaults(self):
        config = ConfigRegistry(namespace="crawling", key="timeout")
        assert config.is_secret is None or config.is_secret is False
        assert config.data_type is None or config.data_type == "string"

    def test_config_secret_flag(self):
        config = ConfigRegistry(
            namespace="outreach",
            key="api_key",
            value={"_value": "secret-123"},
            data_type="string",
            is_secret=True,
        )
        assert config.is_secret is True

    def test_config_table_name(self):
        assert ConfigRegistry.__tablename__ == "config_registry"

    def test_config_unique_constraint_exists(self):
        assert any(
            c.name == "uq_config_namespace_key"
            for c in ConfigRegistry.__table__.constraints
            if hasattr(c, "name")
        )

    def test_config_jsonb_value(self):
        config = ConfigRegistry(
            namespace="scoring",
            key="weights",
            value={"independent": 15, "volume": 20},
            data_type="json",
        )
        assert config.value["independent"] == 15
        assert config.value["volume"] == 20

    def test_config_namespaces(self):
        for ns in ["scoring", "crawling", "outreach"]:
            config = ConfigRegistry(namespace=ns, key="test")
            assert config.namespace == ns


class TestConfigVersion:
    """NIF-138: Configuration versioning model."""

    def test_version_creation(self):
        version = ConfigVersion(
            config_id=uuid.uuid4(),
            version_number=1,
            old_value=None,
            new_value={"_value": 42},
            changed_by="admin",
        )
        assert version.version_number == 1
        assert version.old_value is None
        assert version.new_value == {"_value": 42}
        assert version.changed_by == "admin"

    def test_version_with_old_value(self):
        version = ConfigVersion(
            config_id=uuid.uuid4(),
            version_number=2,
            old_value={"_value": 10},
            new_value={"_value": 20},
            changed_by="system",
        )
        assert version.old_value == {"_value": 10}
        assert version.new_value == {"_value": 20}
        assert version.version_number == 2

    def test_version_table_name(self):
        assert ConfigVersion.__tablename__ == "config_versions"


class TestConfigOverride:
    """NIF-139: Market-specific configuration overrides."""

    def test_override_creation_market(self):
        override = ConfigOverride(
            config_id=uuid.uuid4(),
            scope_type="market",
            scope_value="NYC",
            override_value={"_value": 25},
            priority=10,
            is_active=True,
        )
        assert override.scope_type == "market"
        assert override.scope_value == "NYC"
        assert override.priority == 10
        assert override.is_active is True

    def test_override_creation_cuisine(self):
        override = ConfigOverride(
            config_id=uuid.uuid4(),
            scope_type="cuisine",
            scope_value="Italian",
            override_value={"_value": 1.2},
        )
        assert override.scope_type == "cuisine"
        assert override.scope_value == "Italian"

    def test_override_creation_region(self):
        override = ConfigOverride(
            config_id=uuid.uuid4(),
            scope_type="region",
            scope_value="Northeast",
            override_value={"_value": "aggressive"},
        )
        assert override.scope_type == "region"

    def test_override_scope_types(self):
        for scope in ["market", "cuisine", "region"]:
            override = ConfigOverride(
                config_id=uuid.uuid4(),
                scope_type=scope,
                scope_value="test",
                override_value={"_value": True},
            )
            assert override.scope_type == scope

    def test_override_table_name(self):
        assert ConfigOverride.__tablename__ == "config_overrides"

    def test_override_unique_constraint_exists(self):
        assert any(
            c.name == "uq_config_override_scope"
            for c in ConfigOverride.__table__.constraints
            if hasattr(c, "name")
        )


class TestConfigValidation:
    """NIF-140: Configuration validation framework."""

    def test_validate_string(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "label", "excellent", "string")
        assert is_valid is True
        assert err == ""

    def test_validate_string_rejects_int(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "label", 42, "string")
        assert is_valid is False
        assert "Expected string" in err

    def test_validate_int(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "weight", 15, "int")
        assert is_valid is True

    def test_validate_int_rejects_float(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "weight", 15.5, "int")
        assert is_valid is False
        assert "Expected int" in err

    def test_validate_int_rejects_bool(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "weight", True, "int")
        assert is_valid is False

    def test_validate_float(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "threshold", 0.75, "float")
        assert is_valid is True

    def test_validate_float_accepts_int(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "threshold", 10, "float")
        assert is_valid is True

    def test_validate_float_rejects_string(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "threshold", "high", "float")
        assert is_valid is False

    def test_validate_bool(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "enabled", True, "bool")
        assert is_valid is True

    def test_validate_bool_rejects_int(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "enabled", 1, "bool")
        assert is_valid is False

    def test_validate_json_dict(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "weights", {"a": 1}, "json")
        assert is_valid is True

    def test_validate_json_list(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "items", [1, 2, 3], "json")
        assert is_valid is True

    def test_validate_json_rejects_string(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "data", "not json", "json")
        assert is_valid is False

    def test_validate_invalid_data_type(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "key", "val", "unknown_type")
        assert is_valid is False
        assert "Invalid data_type" in err

    def test_validate_empty_namespace(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("", "key", "val", "string")
        assert is_valid is False
        assert "Namespace" in err

    def test_validate_empty_key(self):
        from src.services.configuration import validate_config
        is_valid, err = validate_config("scoring", "", "val", "string")
        assert is_valid is False
        assert "Key" in err

    def test_valid_data_types_set(self):
        from src.services.configuration import VALID_DATA_TYPES
        assert VALID_DATA_TYPES == {"string", "int", "float", "bool", "json"}


class TestConfigurationService:
    """Test configuration service imports and structure."""

    def test_service_importable(self):
        from src.services.configuration import (
            get_config,
            set_config,
            validate_config,
            list_configs,
            get_config_history,
            create_override,
        )
        assert callable(get_config)
        assert callable(set_config)
        assert callable(validate_config)
        assert callable(list_configs)
        assert callable(get_config_history)
        assert callable(create_override)


class TestConfigurationRouter:
    """NIF-141: Configuration admin API router."""

    def test_router_importable(self):
        from src.api.routers.configuration import router
        assert router.prefix == "/config"

    def test_router_has_list_endpoint(self):
        from src.api.routers.configuration import router
        paths = [r.path for r in router.routes]
        assert "/config" in paths

    def test_router_has_get_endpoint(self):
        from src.api.routers.configuration import router
        paths = [r.path for r in router.routes]
        assert "/config/{namespace}/{key}" in paths

    def test_router_has_set_endpoint(self):
        from src.api.routers.configuration import router
        paths = [r.path for r in router.routes]
        assert "/config/{namespace}/{key}" in paths

    def test_router_has_override_endpoint(self):
        from src.api.routers.configuration import router
        paths = [r.path for r in router.routes]
        assert "/config/overrides" in paths

    def test_router_has_history_endpoint(self):
        from src.api.routers.configuration import router
        paths = [r.path for r in router.routes]
        assert "/config/{config_id}/history" in paths

    def test_router_has_validate_endpoint(self):
        from src.api.routers.configuration import router
        paths = [r.path for r in router.routes]
        assert "/config/validate" in paths

    def test_router_tags(self):
        from src.api.routers.configuration import router
        assert "configuration" in router.tags

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        config_paths = [p for p in paths if "/config" in p]
        assert len(config_paths) > 0
