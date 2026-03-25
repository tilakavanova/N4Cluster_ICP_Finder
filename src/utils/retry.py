"""Retry utilities powered by tenacity."""

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)


def with_retry(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    retryable_exceptions: tuple = (Exception,),
):
    """Decorator factory for retryable operations."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=backoff_base, max=60),
        retry=retry_if_exception_type(retryable_exceptions),
        reraise=True,
    )
