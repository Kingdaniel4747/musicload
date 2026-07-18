"""Image proxy to prevent 429 errors when loading cover art from Google servers."""

import asyncio
import logging
import time
from collections import OrderedDict
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

ALLOWED_HOSTS: frozenset[str] = frozenset({
    "lh3.googleusercontent.com",
    "i.ytimg.com",
    "yt3.ggpht.com",
    "yt3.googleusercontent.com",
})

_MAX_CACHE_ENTRIES = 500
_CACHE_TTL_SECONDS = 3600  # 1 hour
_MAX_CONCURRENT_FETCHES = 5
_FETCH_TIMEOUT_SECONDS = 10


def validate_image_url(url: str) -> str | None:
    """Validate that a URL points to an allowed image host.

    Returns the normalized URL if valid, None otherwise.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    if parsed.hostname not in ALLOWED_HOSTS:
        return None

    return url


class ImageCache:
    """LRU cache for fetched image data with TTL-based expiry."""

    def __init__(self, max_entries: int = _MAX_CACHE_ENTRIES, ttl: int = _CACHE_TTL_SECONDS):
        self._cache: OrderedDict[str, tuple[bytes, str, float]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl

    def get(self, url: str) -> tuple[bytes, str] | None:
        """Return (data, content_type) if cached and not expired, else None."""
        entry = self._cache.get(url)
        if entry is None:
            return None

        data, content_type, timestamp = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._cache[url]
            return None

        # Move to end (most recently used)
        self._cache.move_to_end(url)
        return data, content_type

    def put(self, url: str, data: bytes, content_type: str) -> None:
        """Cache image data, evicting oldest entry if at capacity."""
        if url in self._cache:
            self._cache.move_to_end(url)
            self._cache[url] = (data, content_type, time.monotonic())
            return

        if len(self._cache) >= self._max_entries:
            self._cache.popitem(last=False)

        self._cache[url] = (data, content_type, time.monotonic())


class ImageProxyService:
    """Fetches and caches images from allowed hosts with concurrency control."""

    def __init__(self):
        self._cache = ImageCache()
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)
        self._client = httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def fetch(self, url: str) -> tuple[bytes, str]:
        """Fetch image data, returning (bytes, content_type).

        Uses cache when available, limits concurrent outbound requests.
        Raises ValueError for non-image responses, httpx errors for fetch failures.
        """
        cached = self._cache.get(url)
        if cached is not None:
            return cached

        async with self._semaphore:
            # Re-check cache after acquiring semaphore (another request may have populated it)
            cached = self._cache.get(url)
            if cached is not None:
                return cached

            response = await self._client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                raise ValueError(f"Response is not an image: {content_type}")

            data = response.content
            self._cache.put(url, data, content_type)
            return data, content_type

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
