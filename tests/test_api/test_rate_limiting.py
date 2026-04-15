"""Tests for per-client Redis sliding-window rate limiter (NIF-255).

Covers:
- Rate limit enforced: request N+1 is denied when limit is N
- Sliding window expiry: requests outside the window are not counted
- Redis fallback (fail-open): unavailable Redis allows the request
- 429 response with correct headers via rate_limit dependency
- X-RateLimit-* headers present on allowed responses
- JWT client limit read from APIClient.rate_limit_per_minute
- Legacy/dev callers use default limit of 200
"""

import time
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
import redis

from src.utils.rate_limiter import check_rate_limit


# ---------------------------------------------------------------------------
# Unit tests for check_rate_limit
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    def _make_redis(self, current_count: int, oldest_ts: float | None = None):
        """Build a mock Redis client whose pipeline returns predictable results."""
        mock_redis = MagicMock(spec=redis.Redis)
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        oldest = [(f"{oldest_ts:.6f}", oldest_ts)] if oldest_ts is not None else []
        # pipeline().execute() returns:
        #   [zremrangebyscore, zadd, zcard, zrange, expire]
        mock_pipe.execute.return_value = [None, 1, current_count, oldest, True]
        mock_pipe.__enter__ = lambda s: s
        mock_pipe.__exit__ = MagicMock(return_value=False)
        return mock_redis

    def test_within_limit_allowed(self):
        mock_redis = self._make_redis(current_count=5)
        allowed, remaining, reset_at = check_rate_limit(
            "client_a", limit=10, redis_client=mock_redis
        )
        assert allowed is True
        assert remaining == 5
        assert reset_at > int(time.time())

    def test_at_limit_allowed(self):
        mock_redis = self._make_redis(current_count=10)
        allowed, remaining, _ = check_rate_limit(
            "client_b", limit=10, redis_client=mock_redis
        )
        assert allowed is True
        assert remaining == 0

    def test_over_limit_denied(self):
        mock_redis = self._make_redis(current_count=11)
        allowed, remaining, reset_at = check_rate_limit(
            "client_c", limit=10, redis_client=mock_redis
        )
        assert allowed is False
        assert remaining == 0
        assert reset_at > 0

    def test_reset_at_derived_from_oldest_entry(self):
        now = time.time()
        oldest_ts = now - 30  # 30 seconds into a 60-second window
        mock_redis = self._make_redis(current_count=5, oldest_ts=oldest_ts)
        _, _, reset_at = check_rate_limit(
            "client_d", limit=10, window_seconds=60, redis_client=mock_redis
        )
        # reset_at should be oldest_ts + 60
        assert reset_at == int(oldest_ts) + 60

    def test_redis_unavailable_fails_open(self):
        """When Redis raises RedisError, the request is allowed (fail-open)."""
        mock_redis = MagicMock(spec=redis.Redis)
        mock_redis.pipeline.side_effect = redis.RedisError("connection refused")

        allowed, remaining, reset_at = check_rate_limit(
            "client_e", limit=5, redis_client=mock_redis
        )
        assert allowed is True
        assert remaining == 5  # returns limit as remaining on fail-open

    def test_empty_bucket_reset_at_is_future(self):
        """When the bucket is empty (no oldest entry), reset_at is now + window."""
        mock_redis = self._make_redis(current_count=1, oldest_ts=None)
        now = time.time()
        _, _, reset_at = check_rate_limit(
            "client_f", limit=10, window_seconds=60, redis_client=mock_redis
        )
        assert reset_at >= int(now) + 60


# ---------------------------------------------------------------------------
# Unit tests for the rate_limit FastAPI dependency
# ---------------------------------------------------------------------------


class TestRateLimitDependency:
    @pytest.mark.asyncio
    async def test_allowed_request_sets_headers(self):
        """Allowed request should attach X-RateLimit-* headers and not raise."""
        from fastapi import Response
        from src.api.rate_limit_dep import rate_limit

        response = Response()
        auth = {"mode": "dev", "sub": "dev-mode"}
        mock_session = AsyncMock()

        with patch(
            "src.api.rate_limit_dep.check_rate_limit", return_value=(True, 195, 9999)
        ):
            await rate_limit(
                request=MagicMock(),
                response=response,
                auth=auth,
                session=mock_session,
            )

        assert response.headers["X-RateLimit-Limit"] == "200"
        assert response.headers["X-RateLimit-Remaining"] == "195"
        assert response.headers["X-RateLimit-Reset"] == "9999"

    @pytest.mark.asyncio
    async def test_exceeded_raises_429(self):
        """Exceeded limit raises HTTPException 429 with Retry-After header."""
        from fastapi import Response, HTTPException
        from src.api.rate_limit_dep import rate_limit

        response = Response()
        auth = {"mode": "dev", "sub": "dev-mode"}
        mock_session = AsyncMock()
        future_reset = int(time.time()) + 45

        with patch(
            "src.api.rate_limit_dep.check_rate_limit",
            return_value=(False, 0, future_reset),
        ):
            with pytest.raises(HTTPException) as exc:
                await rate_limit(
                    request=MagicMock(),
                    response=response,
                    auth=auth,
                    session=mock_session,
                )

        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        assert exc.value.headers["X-RateLimit-Remaining"] == "0"

    @pytest.mark.asyncio
    async def test_jwt_client_limit_from_api_client_record(self):
        """JWT auth reads rate_limit_per_minute from APIClient row."""
        from fastapi import Response
        from src.api.rate_limit_dep import rate_limit

        response = Response()
        auth = {"mode": "jwt", "sub": "cid_test"}
        mock_client = MagicMock()
        mock_client.rate_limit_per_minute = 30

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_client
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=result_mock)

        with patch(
            "src.api.rate_limit_dep.check_rate_limit", return_value=(True, 28, 9999)
        ) as mock_rl:
            await rate_limit(
                request=MagicMock(),
                response=response,
                auth=auth,
                session=mock_session,
            )

        # Verify the limit passed to check_rate_limit was 30 (from APIClient)
        mock_rl.assert_called_once_with("cid_test", 30)
        assert response.headers["X-RateLimit-Limit"] == "30"

    @pytest.mark.asyncio
    async def test_legacy_mode_uses_default_limit(self):
        """Legacy/dev mode uses the global default of 200/min."""
        from fastapi import Response
        from src.api.rate_limit_dep import rate_limit

        response = Response()
        auth = {"mode": "legacy", "sub": "legacy"}
        mock_session = AsyncMock()

        with patch(
            "src.api.rate_limit_dep.check_rate_limit", return_value=(True, 199, 9999)
        ) as mock_rl:
            await rate_limit(
                request=MagicMock(),
                response=response,
                auth=auth,
                session=mock_session,
            )

        mock_rl.assert_called_once_with("legacy", 200)
        assert response.headers["X-RateLimit-Limit"] == "200"
