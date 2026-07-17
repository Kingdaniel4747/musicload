"""Reddit plugin for fetching songs from music subreddits."""

import logging
import re
from typing import Pattern

import httpx

from musicload.plugins.base import PluginConfig, PluginError, Song

logger = logging.getLogger(__name__)


class RedditPlugin:
    """Plugin for fetching songs from Reddit music subreddits.

    Supports popular music discovery subreddits like r/listentothis,
    r/Music, r/IndieHeads, etc.

    Title Parsing:
        Expects post titles in format: "Artist - Song [Genre]"
        Also handles variations with double dashes, quotes, and missing genre tags.
    """

    # Regex patterns for title parsing (order matters - most specific first)
    PATTERNS: list[Pattern] = [
        # "Artist Name" - "Song Title" [Genre]
        re.compile(
            r'^["\'](.+?)["\']\s*[-–—]\s*["\'](.+?)["\']\s*\[.+?\]', re.IGNORECASE
        ),
        # Artist - Song [Genre] (most common)
        re.compile(r"^(.+?)\s*[-–—]\s*(.+?)\s*\[.+?\]\s*(?:\(.+?\))?$", re.IGNORECASE),
        # Artist -- Song [Genre] (double dash)
        re.compile(r"^(.+?)\s*--\s*(.+?)\s*\[.+?\]\s*(?:\(.+?\))?$", re.IGNORECASE),
        # Artist - Song (no genre tag)
        re.compile(r"^(.+?)\s*[-–—]\s*(.+?)$", re.IGNORECASE),
    ]

    VALID_SORTS = ["hot", "new", "top", "rising"]
    VALID_TIME_FILTERS = ["hour", "day", "week", "month", "year", "all"]

    @property
    def name(self) -> str:
        return "reddit"

    @property
    def config_schema(self) -> dict:
        return {
            "required": ["subreddit"],
            "optional": {
                "sort": "hot",
                "time_filter": "week",
                "limit": 50,
                "min_score": 0,
                "timeout": 10,
                "user_agent": "dadav/musicload 0.5.0",
            },
        }

    def validate_config(self, config: dict) -> None:
        """Validate configuration."""
        # Required: subreddit
        if "subreddit" not in config:
            raise ValueError("Missing required field: subreddit")

        subreddit = config["subreddit"]
        if not isinstance(subreddit, str):
            raise ValueError("Field 'subreddit' must be a string")

        subreddit = subreddit.strip()
        if not subreddit:
            raise ValueError("Field 'subreddit' cannot be empty")

        # Validate subreddit name format (alphanumeric + underscore)
        subreddit_name = subreddit.replace("r/", "").strip()
        if not re.match(r"^[a-zA-Z0-9_]+$", subreddit_name):
            raise ValueError(
                f"Invalid subreddit name: {subreddit}. "
                "Must contain only letters, numbers, and underscores."
            )

        # Optional: sort
        if "sort" in config:
            sort = config["sort"]
            if not isinstance(sort, str):
                raise ValueError("Field 'sort' must be a string")
            if sort not in self.VALID_SORTS:
                raise ValueError(f"Invalid sort: {sort}. Valid: {self.VALID_SORTS}")

        # Optional: time_filter
        if "time_filter" in config:
            time_filter = config["time_filter"]
            if not isinstance(time_filter, str):
                raise ValueError("Field 'time_filter' must be a string")
            if time_filter not in self.VALID_TIME_FILTERS:
                raise ValueError(
                    f"Invalid time_filter: {time_filter}. "
                    f"Valid: {self.VALID_TIME_FILTERS}"
                )

        # Optional: limit
        if "limit" in config:
            limit = config["limit"]
            if not isinstance(limit, int):
                raise ValueError("Field 'limit' must be an integer")
            if limit < 1 or limit > 100:
                raise ValueError("Field 'limit' must be between 1 and 100")

        # Optional: min_score
        if "min_score" in config:
            min_score = config["min_score"]
            if not isinstance(min_score, int):
                raise ValueError("Field 'min_score' must be an integer")
            if min_score < 0:
                raise ValueError("Field 'min_score' must be non-negative")

        # Optional: timeout
        if "timeout" in config:
            timeout = config["timeout"]
            if not isinstance(timeout, (int, float)):
                raise ValueError("Field 'timeout' must be a number")
            if timeout <= 0:
                raise ValueError("Field 'timeout' must be positive")

        # Optional: user_agent
        if "user_agent" in config:
            user_agent = config["user_agent"]
            if not isinstance(user_agent, str):
                raise ValueError("Field 'user_agent' must be a string")
            if not user_agent.strip():
                raise ValueError("Field 'user_agent' cannot be empty")

    def fetch_songs(self, config: PluginConfig) -> list[Song]:
        """Fetch songs from Reddit subreddit."""
        # Extract and normalize config
        subreddit = config.config["subreddit"].replace("r/", "").strip()
        sort = config.config.get("sort", "hot")
        time_filter = config.config.get("time_filter", "week")
        limit = config.config.get("limit", 50)
        min_score = config.config.get("min_score", 0)
        timeout = config.config.get("timeout", 10)
        user_agent = config.config.get("user_agent", "dadav/musicload 0.5.0")

        # Build URL
        url = self._build_url(subreddit, sort, time_filter, limit)
        logger.info("Fetching from r/%s (sort=%s, limit=%d)", subreddit, sort, limit)

        try:
            # Make request with browser-like headers (Reddit requires these)
            headers = {
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            response = httpx.get(
                url, headers=headers, timeout=timeout, follow_redirects=True
            )
            response.raise_for_status()

            # Parse JSON
            data = response.json()

            # Validate structure
            if not isinstance(data, dict) or "data" not in data:
                raise PluginError("Unexpected Reddit API response structure")

            if "children" not in data["data"]:
                raise PluginError("No posts found in Reddit response")

            posts = data["data"]["children"]
            logger.info("Retrieved %d posts from r/%s", len(posts), subreddit)

            # Parse posts
            songs = []
            skipped_low_score = 0
            skipped_unparseable = 0

            for post in posts:
                post_data = post.get("data", {})
                title = post_data.get("title", "")
                score = post_data.get("score", 0)

                # Apply score filter
                if score < min_score:
                    logger.debug("Skipping low-score post (score=%d): %s", score, title)
                    skipped_low_score += 1
                    continue

                # Parse title
                parsed = self._parse_title(title)
                if not parsed:
                    logger.warning("Could not parse title: %s", title)
                    skipped_unparseable += 1
                    continue

                artist, song_title = parsed
                songs.append(Song(artist=artist, title=song_title))

            logger.info(
                "Parsed %d songs from r/%s (skipped: %d low-score, %d unparseable)",
                len(songs),
                subreddit,
                skipped_low_score,
                skipped_unparseable,
            )

            return songs

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise PluginError(f"Subreddit not found: r/{subreddit}")
            elif e.response.status_code == 429:
                raise PluginError(
                    "Rate limited by Reddit. Try again later or increase timeout."
                )
            elif e.response.status_code == 403:
                raise PluginError(
                    f"Access forbidden to r/{subreddit}. May be private or banned."
                )
            else:
                raise PluginError(f"Reddit API error ({e.response.status_code}): {e}")
        except httpx.TimeoutException:
            raise PluginError(f"Request timed out after {timeout}s")
        except httpx.RequestError as e:
            raise PluginError(f"Network error: {e}")
        except ValueError as e:  # JSON decode error
            raise PluginError(f"Invalid JSON response from Reddit: {e}")
        except Exception as e:
            raise PluginError(f"Error fetching from Reddit: {e}")

    def _build_url(
        self, subreddit: str, sort: str, time_filter: str, limit: int
    ) -> str:
        """Build Reddit JSON API URL."""
        base_url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"

        params = {"limit": min(limit, 100)}  # Reddit max is 100

        # Add time filter only for 'top' sort
        if sort == "top":
            params["t"] = time_filter

        # Construct query string
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base_url}?{query}"

    def _parse_title(self, title: str) -> tuple[str, str] | None:
        """Parse Reddit post title to extract artist and song.

        Args:
            title: Reddit post title

        Returns:
            Tuple of (artist, song) or None if unparseable
        """
        title = title.strip()

        # Try each pattern in order
        for pattern in self.PATTERNS:
            match = pattern.match(title)
            if match:
                artist = self._clean_text(match.group(1))
                song = self._clean_text(match.group(2))

                # Validate non-empty
                if artist and song:
                    return artist, song

        return None

    def _clean_text(self, text: str) -> str:
        """Clean extracted artist/title text.

        Args:
            text: Raw text from regex match

        Returns:
            Cleaned text
        """
        # Remove surrounding quotes
        text = text.strip("'\"")

        # Normalize whitespace
        text = re.sub(r"\s+", " ", text).strip()

        return text
