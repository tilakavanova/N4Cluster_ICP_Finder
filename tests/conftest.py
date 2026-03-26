"""Test configuration and fixtures."""

import asyncio
import os

import pytest

# Prevent real DB/Redis connections during tests
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")


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


@pytest.fixture
def sample_google_places_response():
    return {
        "places": [
            {
                "id": "ChIJ123",
                "displayName": {"text": "Test Restaurant"},
                "formattedAddress": "123 Main St, New York, NY 10001, USA",
                "rating": 4.5,
                "userRatingCount": 200,
                "nationalPhoneNumber": "(555) 123-4567",
                "websiteUri": "https://test.example.com",
                "location": {"latitude": 40.7128, "longitude": -74.0060},
                "primaryType": "italian_restaurant",
                "types": ["italian_restaurant", "restaurant"],
            }
        ]
    }


@pytest.fixture
def sample_yelp_response():
    return {
        "businesses": [
            {
                "id": "yelp-biz-123",
                "name": "Test Restaurant",
                "location": {
                    "address1": "123 Main St",
                    "city": "New York",
                    "state": "NY",
                    "zip_code": "10001",
                },
                "coordinates": {"latitude": 40.7128, "longitude": -74.0060},
                "display_phone": "(555) 123-4567",
                "phone": "+15551234567",
                "rating": 4.5,
                "review_count": 200,
                "categories": [{"alias": "italian", "title": "Italian"}],
                "price": "$$",
                "is_closed": False,
                "url": "https://www.yelp.com/biz/test",
                "transactions": ["delivery", "pickup"],
            }
        ],
        "total": 1,
    }


@pytest.fixture
def multiple_restaurants_for_density():
    return [
        {"id": f"r{i}", "name": f"Restaurant {i}", "lat": 40.71 + i * 0.001, "lng": -74.00 + i * 0.001}
        for i in range(10)
    ]
