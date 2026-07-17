"""Generic RSS/Atom feed plugin."""

import logging
from xml.etree import ElementTree as ET

import httpx

from musicload.plugins.base import Plugin, PluginConfig, PluginError, Song

logger = logging.getLogger(__name__)


class RSSPlugin:
    """Generic RSS/Atom feed parser.

    Supports customizable field mappings for artist/title extraction.
    """

    @property
    def name(self) -> str:
        return "rss"

    @property
    def config_schema(self) -> dict:
        return {
            "required": ["url"],
            "optional": {
                "artist_field": "artist",
                "title_field": "title",
                "timeout": 10,
                "user_agent": "musicload/0.3.0",
            },
        }

    def validate_config(self, config: dict) -> None:
        """Validate configuration."""
        if "url" not in config:
            raise ValueError("Missing required field: url")

        if not isinstance(config["url"], str):
            raise ValueError("Field 'url' must be a string")

        if not config["url"].startswith(("http://", "https://")):
            raise ValueError("Field 'url' must be a valid HTTP(S) URL")

    def fetch_songs(self, config: PluginConfig) -> list[Song]:
        """Fetch songs from RSS/Atom feed."""
        url = config.config["url"]
        artist_field = config.config.get("artist_field", "artist")
        title_field = config.config.get("title_field", "title")
        timeout = config.config.get("timeout", 10)
        user_agent = config.config.get("user_agent", "musicload/0.3.0")

        logger.info("Fetching RSS feed: %s", url)

        try:
            headers = {"User-Agent": user_agent}
            response = httpx.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()

            # Parse XML
            root = ET.fromstring(response.content)

            # Detect feed type (RSS vs Atom)
            if root.tag == "{http://www.w3.org/2005/Atom}feed":
                return self._parse_atom(root, artist_field, title_field)
            else:
                return self._parse_rss(root, artist_field, title_field)

        except httpx.HTTPError as e:
            raise PluginError(f"Failed to fetch RSS feed: {e}")
        except ET.ParseError as e:
            raise PluginError(f"Invalid XML in feed: {e}")
        except Exception as e:
            raise PluginError(f"Error parsing RSS feed: {e}")

    def _parse_rss(
        self,
        root: ET.Element,
        artist_field: str,
        title_field: str,
    ) -> list[Song]:
        """Parse RSS 2.0 feed."""
        songs = []

        # Find all <item> elements
        for item in root.findall(".//item"):
            title = self._get_text(item, title_field)
            artist = self._get_text(item, artist_field)

            if title and artist:
                songs.append(Song(artist=artist, title=title))
            elif title:
                # Try to parse "Artist - Title" format
                parsed = self._parse_combined_title(title)
                if parsed:
                    songs.append(Song(artist=parsed[0], title=parsed[1]))

        logger.info("Parsed %d songs from RSS feed", len(songs))
        return songs

    def _parse_atom(
        self,
        root: ET.Element,
        artist_field: str,
        title_field: str,
    ) -> list[Song]:
        """Parse Atom feed."""
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        songs = []

        for entry in root.findall("atom:entry", ns):
            title = self._get_text(entry, title_field, ns)
            artist = self._get_text(entry, artist_field, ns)

            if title and artist:
                songs.append(Song(artist=artist, title=title))
            elif title:
                parsed = self._parse_combined_title(title)
                if parsed:
                    songs.append(Song(artist=parsed[0], title=parsed[1]))

        logger.info("Parsed %d songs from Atom feed", len(songs))
        return songs

    def _get_text(
        self,
        element: ET.Element,
        field: str,
        namespaces: dict | None = None,
    ) -> str | None:
        """Extract text from XML element."""
        # Try direct child first
        child = element.find(field, namespaces)
        if child is not None and child.text:
            return child.text.strip()

        # Try common namespaces
        common_ns_prefixes = [
            "{http://purl.org/dc/elements/1.1/}",
            "{http://search.yahoo.com/mrss/}",
            "{http://www.itunes.com/dtds/podcast-1.0.dtd}",
        ]

        for ns_prefix in common_ns_prefixes:
            child = element.find(f"{ns_prefix}{field}")
            if child is not None and child.text:
                return child.text.strip()

        return None

    def _parse_combined_title(self, title: str) -> tuple[str, str] | None:
        """Try to parse 'Artist - Title' format."""
        if " - " in title:
            parts = title.split(" - ", 1)
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()
        return None
