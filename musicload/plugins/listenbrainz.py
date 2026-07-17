"""Listenbrainz plugin for weekly recommendations."""

import logging
import re
from html import unescape

import httpx
from bs4 import BeautifulSoup

from musicload.plugins.base import Plugin, PluginConfig, PluginError, Song

logger = logging.getLogger(__name__)


class ListenbrainzPlugin:
    """Plugin for Listenbrainz weekly recommendations."""

    @property
    def name(self) -> str:
        return "listenbrainz"

    @property
    def config_schema(self) -> dict:
        return {
            "required": ["user"],
            "optional": {
                "recommendation_type": "weekly-exploration",
                "timeout": 10,
            },
        }

    def validate_config(self, config: dict) -> None:
        """Validate configuration."""
        if "user" not in config:
            raise ValueError("Missing required field: user")

        if not isinstance(config["user"], str):
            raise ValueError("Field 'user' must be a string")

        if not config["user"].strip():
            raise ValueError("Field 'user' cannot be empty")

        # Validate recommendation_type if provided
        rec_type = config.get("recommendation_type", "weekly-exploration")
        valid_types = ["weekly-exploration", "weekly-jams"]
        if rec_type not in valid_types:
            raise ValueError(
                f"Invalid recommendation_type: {rec_type}. " f"Valid: {valid_types}"
            )

    def fetch_songs(self, config: PluginConfig) -> list[Song]:
        """Fetch songs from Listenbrainz recommendations feed."""
        user = config.config["user"]
        rec_type = config.config.get("recommendation_type", "weekly-exploration")
        timeout = config.config.get("timeout", 10)

        url = (
            f"https://listenbrainz.org/syndication-feed/user/{user}/recommendations"
            f"?recommendation_type={rec_type}"
        )

        logger.info("Fetching Listenbrainz feed for user: %s", user)

        try:
            response = httpx.get(url, timeout=timeout)
            response.raise_for_status()

            # Extract content tag (RSS-like format)
            match = re.search(
                r"(<content[^>]*?.*</content>)",
                response.text,
                re.DOTALL | re.MULTILINE,
            )

            if not match:
                raise PluginError("Could not find <content> tag in feed")

            content_html = unescape(match.group(1))
            soup = BeautifulSoup(content_html, features="html.parser")

            # Extract song items (each <li> contains two <a> tags: artist, title)
            items = soup.select("li")
            songs = []

            for item in items:
                links = item.select("a")
                if len(links) < 2:
                    logger.warning("Skipping malformed item: %s", item)
                    continue

                artist = links[0].text.strip()
                title = links[1].text.strip()

                if artist and title:
                    songs.append(Song(artist=artist, title=title))

            logger.info("Found %d songs in Listenbrainz feed", len(songs))
            return songs

        except httpx.HTTPError as e:
            raise PluginError(f"Failed to fetch Listenbrainz feed: {e}")
        except Exception as e:
            raise PluginError(f"Error parsing Listenbrainz feed: {e}")
