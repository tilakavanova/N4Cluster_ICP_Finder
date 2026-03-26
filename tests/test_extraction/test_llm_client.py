"""Tests for unified LLM client."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.extraction.llm_client import LLMClient


class TestLLMClient:
    def setup_method(self):
        self.client = LLMClient()

    @pytest.mark.asyncio
    async def test_raises_without_any_keys(self):
        with patch("src.extraction.llm_client.settings") as mock:
            mock.openai_api_key = ""
            mock.anthropic_api_key = ""
            client = LLMClient()
            with pytest.raises(RuntimeError, match="No LLM API keys configured"):
                await client.extract_json("test prompt")

    @pytest.mark.asyncio
    async def test_openai_primary(self):
        client = LLMClient()
        mock_openai = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "Test"}'
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
        client._openai_client = mock_openai

        with patch("src.extraction.llm_client.settings") as mock:
            mock.openai_api_key = "sk-test"
            mock.llm_model = "gpt-4o-mini"
            mock.llm_max_tokens = 4000
            result = await client.extract_json("test")
            assert result == {"name": "Test"}

    @pytest.mark.asyncio
    async def test_fallback_to_anthropic(self):
        client = LLMClient()
        client._openai_client = None

        mock_anthropic = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '{"name": "Fallback"}'
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)
        client._anthropic_client = mock_anthropic

        with patch("src.extraction.llm_client.settings") as mock:
            mock.openai_api_key = ""
            mock.anthropic_api_key = "sk-ant-test"
            mock.llm_max_tokens = 4000
            result = await client.extract_json("test")
            assert result == {"name": "Fallback"}

    @pytest.mark.asyncio
    async def test_strips_markdown_fencing(self):
        client = LLMClient()
        client._openai_client = None

        mock_anthropic = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '```json\n{"name": "Test"}\n```'
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)
        client._anthropic_client = mock_anthropic

        with patch("src.extraction.llm_client.settings") as mock:
            mock.openai_api_key = ""
            mock.anthropic_api_key = "sk-ant-test"
            mock.llm_max_tokens = 4000
            result = await client.extract_json("test")
            assert result == {"name": "Test"}
