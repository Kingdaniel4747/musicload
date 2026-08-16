"""Regression tests for web settings and local-library duplicate discovery."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEMP_ROOT = tempfile.TemporaryDirectory()
_ROOT = Path(_TEMP_ROOT.name)
os.environ["MUSICLOAD_DATA_DIR"] = str(_ROOT / "data")
os.environ["MUSICLOAD_DOWNLOAD_DIR"] = str(_ROOT / "music")

from fastapi.testclient import TestClient

from musicload.config import get_config
from musicload.web.app import (
    _find_library_duplicates_sync,
    _preview_ffmpeg_command,
    app,
)


class WebSettingsTests(unittest.TestCase):
    @staticmethod
    def _payload_from(client: TestClient) -> dict:
        response = client.get("/api/settings")
        values = response.json()["values"]
        payload = dict(values)
        payload.update(
            {
                "gotify_token": None,
                "clear_gotify_token": False,
                "session_secret": None,
                "clear_session_secret": False,
            }
        )
        return payload

    def test_home_renders_settings_and_duplicate_controls(self) -> None:
        with TestClient(app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('id="settings-btn"', response.text)
        self.assertIn('id="settings-modal"', response.text)
        self.assertIn('id="find-duplicates-settings-btn"', response.text)
        self.assertNotIn('id="library-duplicates-btn"', response.text)
        for removed_id in (
            "setting-download-dir",
            "setting-replaygain",
            "setting-log-cookie-usage",
            "setting-unavailable-cooldown",
            "setting-lyrics-cache",
            "setting-cookie-retry-delay",
            "setting-web-port",
            "setting-cors-origins",
            "setting-data-dir",
        ):
            self.assertNotIn(f'id="{removed_id}"', response.text)

    def test_play_buttons_are_bound_once_and_active_listenbrainz_is_not_reloaded(self) -> None:
        with TestClient(app) as client:
            response = client.get("/")
            script_responses = [
                client.get(f"/static/app-{section}.js")
                for section in (
                    "core",
                    "search",
                    "downloads",
                    "settings",
                    "explore",
                    "library",
                    "init",
                )
            ]

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('src="/static/app-core.js"', response.text)
        for script in script_responses:
            self.assertEqual(script.status_code, 200, script.text)
        scripts = "\n".join(script.text for script in script_responses)
        self.assertIn('button.dataset.playbackBound = "true"', scripts)
        self.assertNotIn('exploreResults.querySelectorAll(".play-btn")', scripts)
        self.assertNotIn('document.querySelectorAll(".play-btn")', scripts)
        self.assertIn('if (currentTab === "listenbrainz") return;', scripts)
        self.assertIn('audio.src = `/api/preview/${videoId}`;', scripts)
        self.assertNotIn('fetch(`/api/stream-url/${videoId}`)', scripts)

    def test_settings_can_be_saved_and_reset(self) -> None:
        with TestClient(app) as client:
            initial = client.get("/api/settings")
            self.assertEqual(initial.status_code, 200)
            payload = self._payload_from(client)
            payload.update(
                {
                    "audio_format": "mp3",
                    "organization_mode": "album",
                }
            )
            saved = client.put("/api/settings", json=payload)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["values"]["audio_format"], "mp3")
            for removed_key in (
                "download_dir",
                "replaygain",
                "log_cookie_usage",
                "unavailable_cooldown_hours",
                "lyrics_cache_hours",
                "cookie_retry_delay",
                "web_port",
                "cors_origins",
                "data_dir",
            ):
                self.assertNotIn(removed_key, saved.json()["values"])
            self.assertNotIn("session_secret", saved.json()["values"])
            self.assertNotIn("gotify_token", saved.json()["values"])
            self.assertTrue((_ROOT / "data" / "settings.json").is_file())

            reset = client.delete("/api/settings")
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertFalse((_ROOT / "data" / "settings.json").exists())

    def test_navidrome_login_requires_a_long_session_secret(self) -> None:
        with TestClient(app) as client:
            payload = self._payload_from(client)
            payload["navidrome_url"] = "http://navidrome:4533"
            response = client.put("/api/settings", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least 32 characters", response.json()["detail"])

    def test_retired_web_overrides_are_ignored(self) -> None:
        settings_file = _ROOT / "data" / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(
                {
                    "audio_format": "mp3",
                    "download_dir": "ignored-downloads",
                    "replaygain": True,
                    "web_port": 8123,
                }
            ),
            encoding="utf-8",
        )
        try:
            config = get_config()
            self.assertEqual(config.audio_format, "mp3")
            self.assertEqual(config.download_dir, _ROOT / "music")
            self.assertFalse(config.replaygain)
            self.assertEqual(config.web_port, 8000)
        finally:
            settings_file.unlink(missing_ok=True)


class DuplicateFinderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.music_dir = _ROOT / self.id().replace(".", "_")
        self.music_dir.mkdir(parents=True, exist_ok=True)

    def test_finds_matching_words_in_song_names(self) -> None:
        first = self.music_dir / "Artist" / "Album" / "01 - Song.mp3"
        second = self.music_dir / "Artist" / "Album" / "Song copy.mp3"
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"first recording")
        second.write_bytes(b"different recording")

        result = _find_library_duplicates_sync(self.music_dir)

        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["kind"], "name")
        self.assertEqual(result["groups"][0]["matched_words"], ["song"])
        self.assertEqual(len(result["groups"][0]["tracks"]), 2)

    def test_ignores_common_title_additions_without_using_duration(self) -> None:
        first = self.music_dir / "one.mp3"
        second = self.music_dir / "two.flac"
        first.write_bytes(b"first recording")
        second.write_bytes(b"different encoded recording")

        metadata = {
            str(first.relative_to(self.music_dir)): {
                "title": "The Song (Official Audio)",
                "artist": "First Artist",
                "album": "Album One",
                "duration": "3:30",
                "file_exists": True,
            },
            str(second.relative_to(self.music_dir)): {
                "title": "The Song - 2024 Remaster",
                "artist": "Second Artist",
                "album": "Album Two",
                "duration": "7:45",
                "file_exists": True,
            },
        }

        with patch(
            "musicload.web.app._extract_track_info",
            side_effect=lambda entry, _directory: metadata[entry],
        ):
            result = _find_library_duplicates_sync(self.music_dir)

        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["matched_words"], ["song", "the"])
        self.assertEqual(len(result["groups"][0]["tracks"]), 2)

    def test_does_not_group_identical_bytes_with_different_names(self) -> None:
        first = self.music_dir / "First Song.mp3"
        second = self.music_dir / "Different Song.mp3"
        first.write_bytes(b"same bytes")
        second.write_bytes(b"same bytes")

        result = _find_library_duplicates_sync(self.music_dir)

        self.assertEqual(result["groups"], [])

    def test_keeps_meaningful_version_words_distinct(self) -> None:
        first = self.music_dir / "Song.mp3"
        second = self.music_dir / "Song Live.mp3"
        first.write_bytes(b"studio")
        second.write_bytes(b"concert")

        result = _find_library_duplicates_sync(self.music_dir)

        self.assertEqual(result["groups"], [])


class PreviewPlaybackTests(unittest.TestCase):
    def test_ffmpeg_receives_stream_request_headers(self) -> None:
        command = _preview_ffmpeg_command(
            "https://example.test/audio",
            {"User-Agent": "Musicload Test", "Referer": "https://music.youtube.com/"},
        )

        self.assertEqual(command[:2], ["ffmpeg", "-nostdin"])
        headers_index = command.index("-headers")
        self.assertIn("User-Agent: Musicload Test\r\n", command[headers_index + 1])
        self.assertIn(
            "Referer: https://music.youtube.com/\r\n",
            command[headers_index + 1],
        )
        self.assertIn("https://example.test/audio", command)

if __name__ == "__main__":
    unittest.main()
