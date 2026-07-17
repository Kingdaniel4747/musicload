"""Demo plugin - reads song recommendations from a JSON file.

This is an example third-party plugin for musicload. It demonstrates:
- Implementing the Plugin protocol
- Configuration validation
- Fetching songs from a custom source (JSON file)
- Error handling

Use this as a template for your own plugins!
"""

import json
import logging
from pathlib import Path

from musicload.plugins.base import Plugin, PluginConfig, PluginError, Song

logger = logging.getLogger(__name__)


class DemoPlugin:
    """Demo plugin that reads songs from a JSON file.

    This is a simple example to show plugin structure without external dependencies.

    Expected JSON format:
    {
        "songs": [
            {
                "artist": "Artist Name",
                "title": "Song Title",
                "album": "Optional Album Name",
                "duration": 180  # Optional duration in seconds
            }
        ]
    }
    """

    @property
    def name(self) -> str:
        """Plugin type name."""
        return "demo"

    @property
    def config_schema(self) -> dict:
        """Configuration schema.

        Returns a dict describing required and optional config fields.
        """
        return {
            "required": ["file"],
            "optional": {
                "encoding": "utf-8",
                "max_songs": 100,
            },
        }

    def validate_config(self, config: dict) -> None:
        """Validate plugin configuration.

        Args:
            config: Plugin configuration dict

        Raises:
            ValueError: If configuration is invalid
        """
        # Check required fields
        if "file" not in config:
            raise ValueError("Missing required field: file")

        if not isinstance(config["file"], str):
            raise ValueError("Field 'file' must be a string")

        if not config["file"].strip():
            raise ValueError("Field 'file' cannot be empty")

        # Validate file path
        file_path = Path(config["file"])
        if not file_path.exists():
            raise ValueError(f"File does not exist: {file_path}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        # Validate optional fields
        if "encoding" in config:
            if not isinstance(config["encoding"], str):
                raise ValueError("Field 'encoding' must be a string")

        if "max_songs" in config:
            max_songs = config["max_songs"]
            if not isinstance(max_songs, int):
                raise ValueError("Field 'max_songs' must be an integer")
            if max_songs <= 0:
                raise ValueError("Field 'max_songs' must be positive")

    def fetch_songs(self, config: PluginConfig) -> list[Song]:
        """Fetch songs from JSON file.

        Args:
            config: Plugin configuration

        Returns:
            List of Song objects

        Raises:
            PluginError: If file reading or parsing fails
        """
        file_path = Path(config.config["file"])
        encoding = config.config.get("encoding", "utf-8")
        max_songs = config.config.get("max_songs", 100)

        logger.info("Reading songs from: %s", file_path)

        try:
            # Read JSON file
            content = file_path.read_text(encoding=encoding)
            data = json.loads(content)

            # Validate JSON structure
            if not isinstance(data, dict):
                raise PluginError("JSON root must be an object")

            if "songs" not in data:
                raise PluginError("JSON must have 'songs' array")

            if not isinstance(data["songs"], list):
                raise PluginError("'songs' must be an array")

            # Parse songs
            songs = []
            for i, song_data in enumerate(data["songs"][:max_songs]):
                try:
                    # Validate song structure
                    if not isinstance(song_data, dict):
                        logger.warning("Skipping non-object song at index %d", i)
                        continue

                    if "artist" not in song_data or "title" not in song_data:
                        logger.warning(
                            "Skipping song at index %d: missing artist or title", i
                        )
                        continue

                    # Create Song object
                    song = Song(
                        artist=str(song_data["artist"]).strip(),
                        title=str(song_data["title"]).strip(),
                        album=str(song_data["album"]).strip()
                        if "album" in song_data
                        else None,
                        duration_seconds=int(song_data["duration"])
                        if "duration" in song_data and song_data["duration"]
                        else None,
                    )

                    # Validate non-empty
                    if not song.artist or not song.title:
                        logger.warning(
                            "Skipping song at index %d: empty artist or title", i
                        )
                        continue

                    songs.append(song)

                except Exception as e:
                    logger.warning("Failed to parse song at index %d: %s", i, e)
                    continue

            logger.info("Loaded %d songs from JSON file", len(songs))
            return songs

        except json.JSONDecodeError as e:
            raise PluginError(f"Invalid JSON in file: {e}")
        except Exception as e:
            raise PluginError(f"Failed to read song file: {e}")
