import unittest

from musicload.download_paths import _sanitize_path_component, _truncate_to_bytes
from musicload.explore import _normalize_mood_playlist_author
from musicload.search import parse_youtube_url
from musicload.search_utils import parse_duration


class RefactoringRegressionTests(unittest.TestCase):
    def test_parse_youtube_video_urls(self):
        expected = {"type": "video", "id": "dQw4w9WgXcQ"}

        self.assertEqual(
            parse_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            expected,
        )
        self.assertEqual(
            parse_youtube_url("https://youtu.be/dQw4w9WgXcQ"),
            expected,
        )

    def test_parse_youtube_playlist_and_radio_urls(self):
        self.assertEqual(
            parse_youtube_url("https://music.youtube.com/playlist?list=PL123"),
            {"type": "playlist", "id": "PL123"},
        )
        self.assertEqual(
            parse_youtube_url(
                "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVM123"
            ),
            {"type": "video", "id": "dQw4w9WgXcQ"},
        )
        self.assertEqual(
            parse_youtube_url("https://music.youtube.com/playlist?list=RDAMVM123"),
            {"type": "unsupported_radio", "id": "RDAMVM123"},
        )

    def test_parse_youtube_url_rejects_invalid_input(self):
        self.assertIsNone(parse_youtube_url("https://example.com/watch?v=dQw4w9WgXcQ"))
        self.assertIsNone(parse_youtube_url("https://youtube.com/watch?v=too-short"))

    def test_duration_parsing(self):
        self.assertEqual(parse_duration("3:45"), 225)
        self.assertEqual(parse_duration("1:03:45"), 3825)
        self.assertEqual(parse_duration("invalid"), 0)
        self.assertEqual(parse_duration("1:2:3:4"), 0)

    def test_mood_playlist_author_normalization(self):
        self.assertIsNone(_normalize_mood_playlist_author(None))
        self.assertEqual(_normalize_mood_playlist_author(" Artist "), "Artist")
        self.assertEqual(_normalize_mood_playlist_author({"name": "Artist"}), "Artist")
        self.assertEqual(
            _normalize_mood_playlist_author(
                [{"name": "First"}, "Second", {"missing": "name"}]
            ),
            "First, Second",
        )

    def test_download_path_sanitization_preserves_unicode_boundaries(self):
        self.assertEqual(_sanitize_path_component('  A/B:C*?  '), "A-BC")
        self.assertEqual(_sanitize_path_component("..."), "Unknown")
        self.assertEqual(_truncate_to_bytes("ééé", 5), "éé")


if __name__ == "__main__":
    unittest.main()
