"""Generic TTL cache for web API responses.

LRU eviction via OrderedDict, TTL via time.monotonic().
Follows the same pattern as ImageCache in image_proxy.py.
"""

import time
from collections import OrderedDict
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class TtlCache:
    """LRU cache with time-based expiry for API response data."""

    def __init__(self, max_entries: int = 200, ttl_seconds: int = 1800):
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl_seconds

    def get(self, key: str) -> Any | None:
        """Return cached value if present and not expired, else None."""
        entry = self._cache.get(key)
        if entry is None:
            return None

        value, timestamp = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._cache[key]
            return None

        self._cache.move_to_end(key)
        return value

    def put(self, key: str, value: Any) -> None:
        """Cache a value, evicting oldest entry if at capacity."""
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = (value, time.monotonic())
            return

        if len(self._cache) >= self._max_entries:
            self._cache.popitem(last=False)

        self._cache[key] = (value, time.monotonic())

    def cached_call(self, key: str, fn: Callable[[], T]) -> T:
        """Return cached result for key, or call fn(), cache it, and return it."""
        result = self.get(key)
        if result is not None:
            return result

        result = fn()
        self.put(key, result)
        return result
