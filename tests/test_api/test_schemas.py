"""Tests for Pydantic API schemas."""

import pytest
from uuid import uuid4
from datetime import datetime, timezone

from src.api.schemas import (
    RestaurantBase, RestaurantResponse, CrawlJobCreate, CrawlJobResponse,
    ICPScoreResponse, RestaurantFilter, ExportFormat,
)


class TestRestaurantSchemas:
    def test_restaurant_base_minimal(self):
        r = RestaurantBase(name="Test")
        assert r.name == "Test"
        assert r.address is None
        assert r.cuisine_type == []

    def test_restaurant_base_full(self):
        r = RestaurantBase(
            name="Joe's", address="123 Main", city="NY", state="NY",
            zip_code="10001", lat=40.71, lng=-74.0, cuisine_type=["Pizza"],
        )
        assert r.city == "NY"
        assert r.cuisine_type == ["Pizza"]

    def test_restaurant_response_from_attributes(self):
        assert RestaurantResponse.model_config.get("from_attributes") is True


class TestCrawlJobSchemas:
    def test_create_minimal(self):
        job = CrawlJobCreate(source="google_maps", location="New York, NY")
        assert job.query == "restaurants"  # default

    def test_create_custom_query(self):
        job = CrawlJobCreate(source="yelp", query="pizza", location="Chicago, IL")
        assert job.query == "pizza"

    def test_response_model(self):
        job = CrawlJobResponse(
            id=uuid4(), source="google_maps", status="done",
            created_at=datetime.now(timezone.utc),
        )
        assert job.total_items == 0
        assert job.error_message is None


class TestICPScoreSchema:
    def test_defaults(self):
        score = ICPScoreResponse(
            id=uuid4(), restaurant_id=uuid4(),
            scored_at=datetime.now(timezone.utc),
        )
        assert score.total_icp_score == 0.0
        assert score.fit_label == "unknown"
        assert score.delivery_platforms == []


class TestFilterSchemas:
    def test_restaurant_filter_defaults(self):
        f = RestaurantFilter()
        assert f.page == 1
        assert f.page_size == 20

    def test_export_format_defaults(self):
        e = ExportFormat()
        assert e.format == "csv"
