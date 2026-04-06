"""Tests for API key authentication."""

import pytest
from unittest.mock import patch

from src.api.auth import require_api_key


class TestAPIKeyAuth:
    """Test API key authentication dependency."""

    @pytest.mark.asyncio
    async def test_no_api_key_configured_allows_all(self):
        """When API_KEY is empty, all requests are allowed (dev mode)."""
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            result = await require_api_key(api_key=None)
            assert result == "dev-mode"

    @pytest.mark.asyncio
    async def test_no_api_key_configured_ignores_header(self):
        """When API_KEY is empty, even invalid headers pass."""
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = ""
            result = await require_api_key(api_key="random-garbage")
            assert result == "dev-mode"

    @pytest.mark.asyncio
    async def test_valid_api_key(self):
        """Valid API key passes authentication."""
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "test-secret-key-123"
            result = await require_api_key(api_key="test-secret-key-123")
            assert result == "test-secret-key-123"

    @pytest.mark.asyncio
    async def test_invalid_api_key_rejected(self):
        """Invalid API key returns 401."""
        from fastapi import HTTPException
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "test-secret-key-123"
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key(api_key="wrong-key")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_api_key_rejected(self):
        """Missing API key header returns 401."""
        from fastapi import HTTPException
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "test-secret-key-123"
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key(api_key=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_api_key_rejected(self):
        """Empty string API key header returns 401."""
        from fastapi import HTTPException
        with patch("src.api.auth.settings") as mock_settings:
            mock_settings.api_key = "test-secret-key-123"
            with pytest.raises(HTTPException) as exc_info:
                await require_api_key(api_key="")
            assert exc_info.value.status_code == 401
