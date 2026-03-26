"""Tests for data extraction pipeline."""

import pytest
from unittest.mock import AsyncMock, patch

from src.extraction.extractor import RestaurantExtractor


class TestRestaurantExtractor:
    def setup_method(self):
        self.extractor = RestaurantExtractor()

    @pytest.mark.asyncio
    async def test_extract_from_text_short_text_returns_empty(self):
        result = await self.extractor.extract_from_text("too short")
        assert result == {}

    @pytest.mark.asyncio
    async def test_extract_from_text_empty_returns_empty(self):
        result = await self.extractor.extract_from_text("")
        assert result == {}

    @pytest.mark.asyncio
    async def test_extract_from_text_calls_llm(self):
        with patch("src.extraction.extractor.llm_client") as mock_llm:
            mock_llm.extract_json = AsyncMock(return_value={"name": "Test", "cuisine_type": ["Italian"]})
            result = await self.extractor.extract_from_text("A" * 100)
            assert result["name"] == "Test"
            mock_llm.extract_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_from_text_handles_llm_error(self):
        with patch("src.extraction.extractor.llm_client") as mock_llm:
            mock_llm.extract_json = AsyncMock(side_effect=RuntimeError("No API key"))
            result = await self.extractor.extract_from_text("A" * 100)
            assert result == {}

    @pytest.mark.asyncio
    async def test_detect_chain(self):
        with patch("src.extraction.extractor.llm_client") as mock_llm:
            mock_llm.extract_json = AsyncMock(return_value={"is_chain": True, "chain_name": "TestChain", "confidence": 0.9})
            result = await self.extractor.detect_chain("TestChain #5")
            assert result["is_chain"] is True

    @pytest.mark.asyncio
    async def test_detect_chain_handles_error(self):
        with patch("src.extraction.extractor.llm_client") as mock_llm:
            mock_llm.extract_json = AsyncMock(side_effect=RuntimeError("fail"))
            result = await self.extractor.detect_chain("Test")
            assert result["is_chain"] is False

    @pytest.mark.asyncio
    async def test_detect_pos_empty_text(self):
        result = await self.extractor.detect_pos("")
        assert result["has_pos"] is False

    @pytest.mark.asyncio
    async def test_detect_pos_calls_llm(self):
        with patch("src.extraction.extractor.llm_client") as mock_llm:
            mock_llm.extract_json = AsyncMock(return_value={"has_pos": True, "pos_provider": "Toast"})
            result = await self.extractor.detect_pos("Order on Toast")
            assert result["has_pos"] is True

    @pytest.mark.asyncio
    async def test_extract_and_enrich_merges_raw_data(self):
        with patch("src.extraction.extractor.llm_client") as mock_llm:
            mock_llm.extract_json = AsyncMock(side_effect=[
                {},  # extract_from_text (no raw_text)
                {"is_chain": False, "confidence": 0.5},  # detect_chain
            ])
            raw = {"name": "Joe's Pizza", "phone": "555-1234", "rating": 4.5}
            result = await self.extractor.extract_and_enrich(raw)
            assert result["name"] == "Joe's Pizza"
            assert result["phone"] == "555-1234"
