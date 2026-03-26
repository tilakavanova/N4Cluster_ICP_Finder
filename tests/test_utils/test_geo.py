"""Tests for geographic utility functions."""

import pytest
from src.utils.geo import haversine_miles, bounding_box


class TestHaversineMiles:
    def test_same_point_is_zero(self):
        d = haversine_miles(40.7128, -74.0060, 40.7128, -74.0060)
        assert d == pytest.approx(0.0, abs=0.001)

    def test_nyc_to_la(self):
        # ~2,451 miles
        d = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
        assert 2400 < d < 2500

    def test_short_distance(self):
        # Two points ~1 mile apart in NYC
        d = haversine_miles(40.7128, -74.0060, 40.7270, -74.0060)
        assert 0.5 < d < 1.5

    def test_symmetric(self):
        d1 = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
        d2 = haversine_miles(34.0522, -118.2437, 40.7128, -74.0060)
        assert d1 == pytest.approx(d2, abs=0.001)

    def test_known_distance_jfk_to_lga(self):
        # JFK (40.6413, -73.7781) to LGA (40.7769, -73.8740) ~11 miles
        d = haversine_miles(40.6413, -73.7781, 40.7769, -73.8740)
        assert 9 < d < 13

    def test_equator_one_degree(self):
        # 1 degree at equator ~69.1 miles
        d = haversine_miles(0, 0, 0, 1)
        assert 68 < d < 70


class TestBoundingBox:
    def test_returns_four_values(self):
        result = bounding_box(40.7128, -74.0060, 5.0)
        assert len(result) == 4

    def test_box_contains_center(self):
        min_lat, max_lat, min_lng, max_lng = bounding_box(40.7128, -74.0060, 5.0)
        assert min_lat < 40.7128 < max_lat
        assert min_lng < -74.0060 < max_lng

    def test_larger_radius_larger_box(self):
        small = bounding_box(40.7128, -74.0060, 1.0)
        large = bounding_box(40.7128, -74.0060, 10.0)
        assert large[0] < small[0]  # min_lat smaller
        assert large[1] > small[1]  # max_lat larger
        assert large[2] < small[2]  # min_lng smaller
        assert large[3] > small[3]  # max_lng larger

    def test_symmetric_around_center(self):
        min_lat, max_lat, min_lng, max_lng = bounding_box(40.0, -74.0, 5.0)
        assert pytest.approx(40.0 - min_lat, abs=0.001) == pytest.approx(max_lat - 40.0, abs=0.001)

    def test_five_mile_radius_roughly_correct(self):
        min_lat, max_lat, _, _ = bounding_box(40.7128, -74.0060, 5.0)
        lat_span = max_lat - min_lat
        # 5 miles ~ 0.072 degrees latitude each side, so span ~ 0.145
        assert 0.12 < lat_span < 0.17
