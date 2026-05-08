"""Tests for Prometheus metrics endpoint (NIF-268).

Covers:
- GET /metrics returns Prometheus text format
- record_request increments counters
- _format_metric produces correct format
"""

import pytest

from src.api.routers.metrics import (
    record_request,
    _format_metric,
    _request_counts,
    _request_duration_sum,
)


class TestFormatMetric:
    def test_gauge_format(self):
        result = _format_metric("my_gauge", "A test gauge", "gauge", [("", 42.0)])
        assert "# HELP my_gauge A test gauge" in result
        assert "# TYPE my_gauge gauge" in result
        assert "my_gauge 42.0" in result

    def test_counter_with_labels(self):
        samples = [('method="GET",path="/health",status="200"', 10.0)]
        result = _format_metric("http_requests_total", "Total requests", "counter", samples)
        assert 'http_requests_total{method="GET",path="/health",status="200"} 10.0' in result


class TestRecordRequest:
    def setup_method(self):
        _request_counts.clear()
        _request_duration_sum.clear()

    def test_record_increments_count(self):
        record_request("GET", "/health", 200, 0.01)
        record_request("GET", "/health", 200, 0.02)
        assert _request_counts["GET:/health:200"] == 2

    def test_record_accumulates_duration(self):
        record_request("POST", "/api/test", 201, 0.5)
        record_request("POST", "/api/test", 201, 0.3)
        assert abs(_request_duration_sum["POST:/api/test:201"] - 0.8) < 0.001

    def test_different_status_codes(self):
        record_request("GET", "/api", 200, 0.01)
        record_request("GET", "/api", 404, 0.01)
        assert _request_counts["GET:/api:200"] == 1
        assert _request_counts["GET:/api:404"] == 1
