"""Tests for LLM Prompt Versioning (NIF-264)."""

import uuid

import pytest

from src.db.models import LLMPromptTemplate


class TestLLMPromptTemplateModel:
    """NIF-264: LLMPromptTemplate model."""

    def test_model_creation(self):
        t = LLMPromptTemplate(
            name="lead_qualification",
            version=1,
            prompt_text="You are an AI that qualifies leads...",
            model_id="gpt-4o-mini",
            temperature=0.7,
            max_tokens=4000,
            is_active=True,
            created_by="admin",
        )
        assert t.name == "lead_qualification"
        assert t.version == 1
        assert t.prompt_text == "You are an AI that qualifies leads..."
        assert t.model_id == "gpt-4o-mini"
        assert t.temperature == 0.7
        assert t.max_tokens == 4000
        assert t.is_active is True

    def test_model_defaults(self):
        t = LLMPromptTemplate(
            name="test",
            prompt_text="test prompt",
        )
        assert t.version is None or t.version == 1
        assert t.model_id is None or t.model_id == "gpt-4o-mini"
        assert t.temperature is None or t.temperature == 0.7
        assert t.max_tokens is None or t.max_tokens == 4000
        assert t.is_active is None or t.is_active is True
        assert t.created_by is None or t.created_by == "system"

    def test_table_name(self):
        assert LLMPromptTemplate.__tablename__ == "llm_prompt_templates"

    def test_name_indexed(self):
        col = LLMPromptTemplate.__table__.c.name
        assert col.index is True

    def test_is_active_indexed(self):
        col = LLMPromptTemplate.__table__.c.is_active
        assert col.index is True

    def test_unique_constraint_name_version(self):
        constraints = LLMPromptTemplate.__table__.constraints
        uq_names = [c.name for c in constraints if hasattr(c, 'name') and c.name]
        assert "uq_prompt_name_version" in uq_names

    def test_metadata_column(self):
        t = LLMPromptTemplate(
            name="test",
            prompt_text="test",
            metadata_={"tags": ["production", "v1"]},
        )
        assert t.metadata_ == {"tags": ["production", "v1"]}

    def test_model_before_audit_log(self):
        """LLMPromptTemplate should be defined in models.py (import check)."""
        from src.db.models import LLMPromptTemplate, AuditLog
        assert LLMPromptTemplate is not None
        assert AuditLog is not None


class TestPromptVersioningServiceImports:
    """NIF-264: Verify service functions are importable."""

    def test_get_active_prompt_importable(self):
        from src.services.prompt_versioning import get_active_prompt
        assert callable(get_active_prompt)

    def test_create_prompt_importable(self):
        from src.services.prompt_versioning import create_prompt
        assert callable(create_prompt)

    def test_update_prompt_importable(self):
        from src.services.prompt_versioning import update_prompt
        assert callable(update_prompt)

    def test_list_prompts_importable(self):
        from src.services.prompt_versioning import list_prompts
        assert callable(list_prompts)

    def test_get_prompt_history_importable(self):
        from src.services.prompt_versioning import get_prompt_history
        assert callable(get_prompt_history)

    def test_rollback_prompt_importable(self):
        from src.services.prompt_versioning import rollback_prompt
        assert callable(rollback_prompt)


class TestPromptsRouter:
    """NIF-264: Prompts router registration and endpoints."""

    def test_router_importable(self):
        from src.api.routers.prompts import router
        assert router.prefix == "/prompts"

    def test_router_has_list_endpoint(self):
        from src.api.routers.prompts import router
        paths = [r.path for r in router.routes]
        assert "/prompts" in paths

    def test_router_has_get_by_name_endpoint(self):
        from src.api.routers.prompts import router
        paths = [r.path for r in router.routes]
        assert "/prompts/{name}" in paths

    def test_router_has_create_endpoint(self):
        from src.api.routers.prompts import router
        # POST /prompts
        methods = {}
        for route in router.routes:
            if hasattr(route, 'methods'):
                methods[route.path] = route.methods
        assert "POST" in methods.get("/prompts", set())

    def test_router_has_update_endpoint(self):
        from src.api.routers.prompts import router
        methods = {}
        for route in router.routes:
            if hasattr(route, 'methods'):
                methods[route.path] = route.methods
        assert "PATCH" in methods.get("/prompts/{name}", set())

    def test_router_has_history_endpoint(self):
        from src.api.routers.prompts import router
        paths = [r.path for r in router.routes]
        assert "/prompts/{name}/history" in paths

    def test_router_has_rollback_endpoint(self):
        from src.api.routers.prompts import router
        paths = [r.path for r in router.routes]
        assert "/prompts/{name}/rollback" in paths

    def test_router_registered_in_app(self):
        from src.main import app
        paths = [r.path for r in app.routes]
        prompt_paths = [p for p in paths if "/prompts" in p]
        assert len(prompt_paths) > 0

    def test_router_tags(self):
        from src.api.routers.prompts import router
        assert "prompts" in router.tags

    def test_template_to_dict_helper(self):
        from src.api.routers.prompts import _template_to_dict
        t = LLMPromptTemplate(
            id=uuid.uuid4(),
            name="test",
            version=1,
            prompt_text="test prompt",
            model_id="gpt-4o-mini",
            temperature=0.7,
            max_tokens=4000,
            metadata_={"key": "value"},
            is_active=True,
            created_by="admin",
        )
        d = _template_to_dict(t)
        assert d["name"] == "test"
        assert d["version"] == 1
        assert d["prompt_text"] == "test prompt"
        assert d["model_id"] == "gpt-4o-mini"
        assert d["temperature"] == 0.7
        assert d["max_tokens"] == 4000
        assert d["metadata"] == {"key": "value"}
        assert d["is_active"] is True
        assert d["created_by"] == "admin"


class TestPromptMigration:
    """NIF-264: Migration file exists."""

    def _load_migration(self):
        import importlib.util
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "alembic", "versions", "026_add_llm_prompt_templates.py",
        )
        spec = importlib.util.spec_from_file_location("migration_026", os.path.abspath(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_migration_file_importable(self):
        mod = self._load_migration()
        assert mod.revision == "026"
        assert mod.down_revision == "025"

    def test_migration_has_upgrade(self):
        mod = self._load_migration()
        assert callable(mod.upgrade)

    def test_migration_has_downgrade(self):
        mod = self._load_migration()
        assert callable(mod.downgrade)
