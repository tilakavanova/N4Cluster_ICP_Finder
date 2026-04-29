"""Tests for HubSpot communication log sync (NIF-237).

Covers:
- sync_communication_log creates engagement for each type
- sync_communication_log returns None when disabled
- sync_communication_log returns None when no contact_id
- sync_deal_stage updates deal stage
- sync_deal_stage returns None when no deal_id
- pull_hubspot_activities returns engagement list
- pull_hubspot_activities returns empty list when disabled
- delete_contact returns True on success
"""

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import pytest

from src.services.hubspot import HubSpotService


def _make_lead(**overrides):
    lead = MagicMock()
    lead.id = overrides.get("id", "lead-uuid-1")
    lead.first_name = overrides.get("first_name", "Joe")
    lead.last_name = overrides.get("last_name", "Pizza")
    lead.email = overrides.get("email", "joe@pizza.com")
    lead.company = overrides.get("company", "Joe's Pizza")
    lead.hubspot_contact_id = overrides.get("hubspot_contact_id", "12345")
    lead.hubspot_deal_id = overrides.get("hubspot_deal_id", "67890")
    return lead


class TestSyncCommunicationLog:
    @pytest.mark.asyncio
    async def test_creates_email_engagement(self):
        svc = HubSpotService()
        svc.enabled = True
        lead = _make_lead()

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"engagement": {"id": 999}}

        with patch("src.services.hubspot.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await svc.sync_communication_log(
                lead, activity_type="email", outcome="sent", channel="email", notes="Follow-up"
            )

        assert result is not None
        assert result["engagement_id"] == "999"
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["engagement"]["type"] == "EMAIL"

    @pytest.mark.asyncio
    async def test_creates_call_engagement(self):
        svc = HubSpotService()
        svc.enabled = True
        lead = _make_lead()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"engagement": {"id": 1000}}

        with patch("src.services.hubspot.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await svc.sync_communication_log(
                lead, activity_type="call", outcome="connected", notes="Discussed demo"
            )

        assert result is not None
        assert result["engagement_id"] == "1000"

    @pytest.mark.asyncio
    async def test_creates_meeting_engagement(self):
        svc = HubSpotService()
        svc.enabled = True
        lead = _make_lead()

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"engagement": {"id": 1001}}

        with patch("src.services.hubspot.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await svc.sync_communication_log(
                lead, activity_type="meeting", outcome="completed", notes="Onboarding call"
            )

        assert result is not None
        assert result["engagement_id"] == "1001"

    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self):
        svc = HubSpotService()
        svc.enabled = False
        result = await svc.sync_communication_log(
            _make_lead(), activity_type="email"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_contact_id(self):
        svc = HubSpotService()
        svc.enabled = True
        lead = _make_lead(hubspot_contact_id=None)
        result = await svc.sync_communication_log(
            lead, activity_type="email"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_failure(self):
        svc = HubSpotService()
        svc.enabled = True
        lead = _make_lead()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("src.services.hubspot.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # Disable retry for test speed
            result = await svc.sync_communication_log.__wrapped__(
                svc, lead, activity_type="email"
            )

        assert result is None


class TestSyncDealStage:
    @pytest.mark.asyncio
    async def test_updates_deal_stage(self):
        svc = HubSpotService()
        svc.enabled = True
        lead = _make_lead()

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("src.services.hubspot.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.patch.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await svc.sync_deal_stage(lead, "closedwon")

        assert result is not None
        assert result["deal_id"] == "67890"
        assert result["new_stage"] == "closedwon"

    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self):
        svc = HubSpotService()
        svc.enabled = False
        result = await svc.sync_deal_stage(_make_lead(), "closedwon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_deal_id(self):
        svc = HubSpotService()
        svc.enabled = True
        lead = _make_lead(hubspot_deal_id=None)
        result = await svc.sync_deal_stage(lead, "closedwon")
        assert result is None


class TestPullHubspotActivities:
    @pytest.mark.asyncio
    async def test_returns_activities(self):
        svc = HubSpotService()
        svc.enabled = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "engagement": {"id": 1, "type": "EMAIL", "timestamp": 1700000000000, "createdAt": 1700000000000},
                    "metadata": {"subject": "Hello"},
                },
                {
                    "engagement": {"id": 2, "type": "CALL", "timestamp": 1700001000000, "createdAt": 1700001000000},
                    "metadata": {"body": "Called"},
                },
            ]
        }

        with patch("src.services.hubspot.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await svc.pull_hubspot_activities("12345")

        assert len(results) == 2
        assert results[0]["type"] == "EMAIL"
        assert results[1]["type"] == "CALL"

    @pytest.mark.asyncio
    async def test_returns_empty_when_disabled(self):
        svc = HubSpotService()
        svc.enabled = False
        result = await svc.pull_hubspot_activities("12345")
        assert result == []


class TestDeleteContact:
    @pytest.mark.asyncio
    async def test_deletes_contact(self):
        svc = HubSpotService()
        svc.enabled = True

        mock_resp = MagicMock()
        mock_resp.status_code = 204

        with patch("src.services.hubspot.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.delete.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await svc.delete_contact("12345")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_disabled(self):
        svc = HubSpotService()
        svc.enabled = False
        result = await svc.delete_contact("12345")
        assert result is False
