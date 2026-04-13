"""Tests for NIF-227: Unsubscribe management endpoints.

Covers:
- GET /unsubscribe/{token} returns 200 with confirmation form for valid token
- GET /unsubscribe/{token} returns 404 HTML for expired/unknown token
- POST /unsubscribe/{token} sets Lead.email_opt_out=True
- POST /unsubscribe/{token} creates TrackerEvent(event_type="unsubscribe")
- POST /unsubscribe/{token} returns 404 for invalid token
- POST /unsubscribe/one-click sets Lead.email_opt_out for known email
- POST /unsubscribe/one-click returns 404 for missing email param
- generate_unsubscribe_token stores data in Redis and returns token
- get_unsubscribe_data retrieves stored token data
- get_unsubscribe_data returns None for missing token
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.routers.unsubscribe import generate_unsubscribe_token, get_unsubscribe_data


# ── Token utility tests ──────────────────────────────────────────────────────

def test_generate_unsubscribe_token_stores_and_returns_token():
    mock_redis = MagicMock()
    lead_id = str(uuid.uuid4())
    token = generate_unsubscribe_token(lead_id, redis_client=mock_redis)
    assert isinstance(token, str)
    assert len(token) > 8
    mock_redis.setex.assert_called_once()
    key, ttl, value = mock_redis.setex.call_args[0]
    assert key == f"unsub:{token}"
    stored = json.loads(value)
    assert stored["lead_id"] == lead_id


def test_generate_unsubscribe_token_includes_target_and_campaign():
    mock_redis = MagicMock()
    lead_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    campaign_id = str(uuid.uuid4())
    token = generate_unsubscribe_token(lead_id, target_id, campaign_id, redis_client=mock_redis)
    _, _, value = mock_redis.setex.call_args[0]
    stored = json.loads(value)
    assert stored["target_id"] == target_id
    assert stored["campaign_id"] == campaign_id


def test_get_unsubscribe_data_returns_stored():
    lead_id = str(uuid.uuid4())
    data = {"lead_id": lead_id, "target_id": None, "campaign_id": None}
    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(data)
    result = get_unsubscribe_data("sometoken", redis_client=mock_redis)
    assert result == data


def test_get_unsubscribe_data_returns_none_for_missing():
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    result = get_unsubscribe_data("missingtoken", redis_client=mock_redis)
    assert result is None


def test_get_unsubscribe_data_returns_none_for_invalid_json():
    mock_redis = MagicMock()
    mock_redis.get.return_value = "not-json"
    result = get_unsubscribe_data("badtoken", redis_client=mock_redis)
    assert result is None


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from src.api.routers.unsubscribe import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _make_valid_token_data(lead_id=None):
    return {
        "lead_id": str(lead_id or uuid.uuid4()),
        "target_id": str(uuid.uuid4()),
        "campaign_id": str(uuid.uuid4()),
    }


def test_get_unsubscribe_valid_token_returns_200(client):
    data = _make_valid_token_data()
    with patch("src.api.routers.unsubscribe.get_unsubscribe_data", return_value=data):
        resp = client.get("/unsubscribe/validtoken")
    assert resp.status_code == 200
    assert "Unsubscribe" in resp.text


def test_get_unsubscribe_valid_token_html_has_form(client):
    data = _make_valid_token_data()
    with patch("src.api.routers.unsubscribe.get_unsubscribe_data", return_value=data):
        resp = client.get("/unsubscribe/validtoken")
    assert "<form" in resp.text
    assert 'method="post"' in resp.text


def test_get_unsubscribe_invalid_token_returns_404(client):
    with patch("src.api.routers.unsubscribe.get_unsubscribe_data", return_value=None):
        resp = client.get("/unsubscribe/expiredtoken")
    assert resp.status_code == 404
    assert "expired" in resp.text.lower() or "invalid" in resp.text.lower()


def test_post_unsubscribe_sets_opt_out(client):
    lead = MagicMock()
    lead.id = uuid.uuid4()
    lead.email_opt_out = False
    data = _make_valid_token_data(lead_id=lead.id)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=lead)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    with patch("src.api.routers.unsubscribe.get_unsubscribe_data", return_value=data), \
         patch("src.api.routers.unsubscribe.async_session", return_value=mock_session):
        resp = client.post("/unsubscribe/validtoken")

    assert resp.status_code == 200
    assert lead.email_opt_out is True


def test_post_unsubscribe_creates_tracker_event(client):
    from src.db.models import TrackerEvent
    lead = MagicMock()
    lead.id = uuid.uuid4()
    lead.email_opt_out = False
    data = _make_valid_token_data(lead_id=lead.id)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=lead)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    with patch("src.api.routers.unsubscribe.get_unsubscribe_data", return_value=data), \
         patch("src.api.routers.unsubscribe.async_session", return_value=mock_session):
        client.post("/unsubscribe/validtoken")

    mock_session.add.assert_called_once()
    added = mock_session.add.call_args[0][0]
    assert isinstance(added, TrackerEvent)
    assert added.event_type == "unsubscribe"


def test_post_unsubscribe_invalid_token_returns_404(client):
    with patch("src.api.routers.unsubscribe.get_unsubscribe_data", return_value=None):
        resp = client.post("/unsubscribe/badtoken")
    assert resp.status_code == 404


def test_post_unsubscribe_confirmation_page(client):
    lead = MagicMock()
    lead.id = uuid.uuid4()
    data = _make_valid_token_data(lead_id=lead.id)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=lead)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    with patch("src.api.routers.unsubscribe.get_unsubscribe_data", return_value=data), \
         patch("src.api.routers.unsubscribe.async_session", return_value=mock_session):
        resp = client.post("/unsubscribe/validtoken")

    assert "unsubscribed" in resp.text.lower() or "successfully" in resp.text.lower()


# ── One-click unsubscribe ─────────────────────────────────────────────────────

def test_one_click_unsubscribe_sets_opt_out(client):
    lead = MagicMock()
    lead.id = uuid.uuid4()
    lead.email = "owner@pasta.com"
    lead.email_opt_out = False

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = lead

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    with patch("src.api.routers.unsubscribe.async_session", return_value=mock_session):
        resp = client.post(
            "/unsubscribe/one-click",
            data={"List-Unsubscribe": "One-Click", "email": "owner@pasta.com"},
        )

    assert resp.status_code == 200
    assert lead.email_opt_out is True


def test_one_click_unsubscribe_missing_email_returns_422(client):
    resp = client.post(
        "/unsubscribe/one-click",
        data={"List-Unsubscribe": "One-Click"},
    )
    assert resp.status_code == 422


def test_one_click_unsubscribe_unknown_lead_returns_200(client):
    """Should return 200 even if lead not found (silent success for privacy)."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    with patch("src.api.routers.unsubscribe.async_session", return_value=mock_session):
        resp = client.post(
            "/unsubscribe/one-click",
            data={"List-Unsubscribe": "One-Click", "email": "unknown@example.com"},
        )

    assert resp.status_code == 200
