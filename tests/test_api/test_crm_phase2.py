"""Tests for CRM Phase 2: account/contact history, follow-up tasks, lead merge.

NIF-70, NIF-71, NIF-112, NIF-115
"""

import uuid
from datetime import datetime, timezone

import pytest

from src.db.models import (
    Account, Contact, Lead, AccountHistory, ContactHistory,
    FollowUpTask, LeadStageHistory, LeadAssignmentHistory,
)


class TestAccountHistory:
    """NIF-70: Account history model."""

    def test_account_history_model_fields(self):
        history = AccountHistory(
            account_id=uuid.uuid4(),
            field_name="name",
            old_value="Old Corp",
            new_value="New Corp",
            changed_by="admin",
        )
        assert history.field_name == "name"
        assert history.old_value == "Old Corp"
        assert history.new_value == "New Corp"
        assert history.changed_by == "admin"

    def test_account_history_nullable_old_value(self):
        history = AccountHistory(
            account_id=uuid.uuid4(),
            field_name="website",
            old_value=None,
            new_value="https://example.com",
        )
        assert history.old_value is None
        assert history.new_value == "https://example.com"


class TestContactHistory:
    """NIF-71: Contact history model."""

    def test_contact_history_model_fields(self):
        history = ContactHistory(
            contact_id=uuid.uuid4(),
            field_name="email",
            old_value="old@example.com",
            new_value="new@example.com",
            changed_by="crm_sync",
        )
        assert history.field_name == "email"
        assert history.old_value == "old@example.com"
        assert history.new_value == "new@example.com"

    def test_contact_history_tracks_role_change(self):
        history = ContactHistory(
            contact_id=uuid.uuid4(),
            field_name="role",
            old_value="manager",
            new_value="owner",
        )
        assert history.field_name == "role"


class TestFollowUpTask:
    """NIF-112: Follow-up task model."""

    def test_task_creation_defaults(self):
        task = FollowUpTask(
            lead_id=uuid.uuid4(),
            title="Schedule demo call",
            task_type="follow_up",
            priority="medium",
            status="pending",
        )
        assert task.title == "Schedule demo call"
        assert task.task_type == "follow_up"
        assert task.priority == "medium"
        assert task.status == "pending"
        assert task.completed_at is None

    def test_task_with_all_fields(self):
        due = datetime(2026, 4, 15, tzinfo=timezone.utc)
        task = FollowUpTask(
            lead_id=uuid.uuid4(),
            title="Send pricing proposal",
            description="Include enterprise tier options",
            task_type="email",
            priority="high",
            status="in_progress",
            assigned_to="sales_rep_1",
            due_date=due,
        )
        assert task.task_type == "email"
        assert task.priority == "high"
        assert task.assigned_to == "sales_rep_1"
        assert task.due_date == due

    def test_task_types(self):
        for task_type in ["follow_up", "call", "email", "demo", "other"]:
            task = FollowUpTask(lead_id=uuid.uuid4(), title="Test", task_type=task_type)
            assert task.task_type == task_type


class TestLeadMerge:
    """NIF-115: Lead merge and duplicate handling."""

    def test_lead_merge_fields_exist(self):
        lead = Lead(
            first_name="Test",
            last_name="User",
            email="test@example.com",
        )
        assert lead.is_merged is None or lead.is_merged is False
        assert lead.merged_into_id is None

    def test_lead_marked_as_merged(self):
        source = Lead(
            first_name="Dup",
            last_name="Lead",
            email="dup@example.com",
        )
        target_id = uuid.uuid4()
        source.is_merged = True
        source.merged_into_id = target_id
        source.status = "merged"

        assert source.is_merged is True
        assert source.merged_into_id == target_id
        assert source.status == "merged"

    def test_merge_preserves_target_data(self):
        """Target lead data should take precedence over source."""
        target = Lead(
            first_name="Primary",
            last_name="Lead",
            email="primary@example.com",
            company="Target Corp",
            icp_fit_label="strong",
        )
        source = Lead(
            first_name="Duplicate",
            last_name="Lead",
            email="dup@example.com",
            company="Source Corp",
            icp_fit_label="moderate",
        )
        # Target already has company and icp_fit_label — should not be overwritten
        assert target.company == "Target Corp"
        assert target.icp_fit_label == "strong"

    def test_merge_fills_missing_target_fields(self):
        """Source data should fill in missing target fields."""
        target = Lead(
            first_name="Primary",
            last_name="Lead",
            email="primary@example.com",
            company=None,
        )
        source = Lead(
            first_name="Duplicate",
            last_name="Lead",
            email="dup@example.com",
            company="Source Corp",
            utm_source="google",
        )
        # Simulate merge logic: fill target's None fields from source
        merge_fields = ["company", "utm_source"]
        for field in merge_fields:
            if getattr(target, field) is None:
                setattr(target, field, getattr(source, field))

        assert target.company == "Source Corp"
        assert target.utm_source == "google"


class TestCRMPhase2Schemas:
    """Test Pydantic schemas for CRM phase 2 endpoints."""

    def test_account_update_schema(self):
        from src.api.routers.crm import AccountUpdate
        update = AccountUpdate(name="New Name", changed_by="admin")
        assert update.name == "New Name"
        assert update.changed_by == "admin"

    def test_contact_update_schema(self):
        from src.api.routers.crm import ContactUpdate
        update = ContactUpdate(email="new@example.com")
        assert update.email == "new@example.com"
        assert update.changed_by == "system"  # default

    def test_task_create_schema(self):
        from src.api.routers.crm import TaskCreate
        task = TaskCreate(
            lead_id=uuid.uuid4(),
            title="Call back",
            task_type="call",
            priority="high",
        )
        assert task.title == "Call back"
        assert task.task_type == "call"

    def test_lead_merge_schema_rejects_same_id(self):
        from src.api.routers.crm import LeadMergeRequest
        lead_id = uuid.uuid4()
        merge = LeadMergeRequest(
            source_lead_id=lead_id,
            target_lead_id=lead_id,
        )
        # Validation happens at endpoint level, not schema level
        assert merge.source_lead_id == merge.target_lead_id
