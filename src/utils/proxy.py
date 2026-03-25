"""Proxy pool manager for crawlers."""

import itertools
from src.config import settings


class ProxyPool:
    """Round-robin proxy rotation."""

    def __init__(self, proxies: list[str] | None = None):
        self._proxies = proxies or settings.proxy_pool
        self._cycle = itertools.cycle(self._proxies) if self._proxies else None

    def next_proxy(self) -> str | None:
        if self._cycle is None:
            return None
        return next(self._cycle)

    @property
    def has_proxies(self) -> bool:
        return bool(self._proxies)


proxy_pool = ProxyPool()
