"""Test configuration and fixtures."""

import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_restaurant():
    return {
        "id": "test-uuid-1234",
        "name": "Joe's Pizza",
        "address": "123 Main St",
        "city": "New York",
        "state": "NY",
        "zip_code": "10001",
        "lat": 40.7128,
        "lng": -74.0060,
        "phone": "(555) 123-4567",
        "website": "https://joespizza.example.com",
        "cuisine_type": ["Pizza", "Italian"],
        "is_chain": False,
        "review_count": 250,
        "rating": 4.5,
    }


@pytest.fixture
def sample_source_records():
    return [
        {
            "source": "google_maps",
            "source_url": "https://maps.google.com/...",
            "raw_data": {"name": "Joe's Pizza", "rating": 4.5},
            "extracted_data": {"cuisine_type": ["Pizza"]},
        },
        {
            "source": "doordash",
            "source_url": "https://doordash.com/store/...",
            "raw_data": {"name": "Joe's Pizza"},
            "extracted_data": None,
            "has_delivery": True,
            "delivery_platform": "doordash",
        },
    ]


@pytest.fixture
def sample_chain_restaurant():
    return {
        "id": "test-uuid-5678",
        "name": "McDonald's",
        "address": "456 Broadway",
        "city": "New York",
        "state": "NY",
        "zip_code": "10002",
        "lat": 40.7200,
        "lng": -73.9980,
        "review_count": 500,
        "rating": 3.5,
    }
