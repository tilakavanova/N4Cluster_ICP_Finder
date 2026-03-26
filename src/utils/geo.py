"""Geographic utilities for distance calculations."""

import math

EARTH_RADIUS_MILES = 3958.8
EARTH_RADIUS_KM = 6371.0
KM_PER_MILE = 1.60934


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate haversine distance between two points in miles."""
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def bounding_box(lat: float, lng: float, radius_miles: float) -> tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lng, max_lng) bounding box for a radius around a point.

    Used as a fast pre-filter before precise haversine calculation.
    """
    lat_delta = radius_miles / 69.0  # ~69 miles per degree of latitude
    lng_delta = radius_miles / (69.0 * math.cos(math.radians(lat)))
    return (
        lat - lat_delta,
        lat + lat_delta,
        lng - lng_delta,
        lng + lng_delta,
    )
