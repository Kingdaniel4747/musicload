"""Chart selection must not turn unrelated playlists into alleged top charts."""

import unittest
from unittest.mock import patch

from musicload.chart_sources import chart_playlists
from musicload.search import get_charts


def reference(playlist_id, title):
    return {"playlistId": playlist_id, "title": title}


def song(video_id, title="Song", video_type="MUSIC_VIDEO_TYPE_ATV"):
    return {
        "videoId": video_id, "title": title, "videoType": video_type,
        "artists": [{"name": "Artist"}], "duration": "3:20", "thumbnails": [],
    }


class ChartTests(unittest.TestCase):
    def test_weekly_chart_beats_first_working_playlist_and_preserves_ranks(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"videos": [
                reference("PLunrelated", "Discover new music"),
                reference("PLdaily", "Daily Top Music Videos - Germany"),
                reference("PLweekly", "Weekly Top Music Videos - Germany"),
            ]}
            yt.get_playlist.return_value = {
                "author": {"id": "FEmusic_charts", "name": "YouTube Charts"},
                "title": "Weekly Top Music Videos - Germany", "tracks": [
                    song("first"), song("ugc", video_type="MUSIC_VIDEO_TYPE_UGC"),
                    song("third", video_type="MUSIC_VIDEO_TYPE_OMV"),
                ],
            }
            charts = get_charts("DE")
            constructor.assert_called_once_with(language="en")
            yt.get_charts.assert_called_once_with("DE")
            yt.get_playlist.assert_called_once_with("PLweekly", limit=100)
            self.assertEqual([track.rank for track in charts.tracks], ["1", "3"])
            self.assertEqual(charts.source_title, "Weekly Top Music Videos - Germany")
            self.assertEqual(charts.source_url, "https://music.youtube.com/playlist?list=PLweekly")

    def test_empty_weekly_chart_can_fall_back_to_a_named_daily_chart(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"videos": [
                reference("PLdaily", "Daily Top Music Videos - Germany"),
                reference("PLweekly", "Weekly Top Music Videos - Germany"),
            ]}
            yt.get_playlist.side_effect = [
                {"author": {"id": "FEmusic_charts"}, "title": "Weekly Top Music Videos - Germany", "tracks": []},
                {"author": {"id": "FEmusic_charts"}, "title": "Daily Top Music Videos - Germany", "tracks": [song("daily")]},
            ]
            charts = get_charts("DE")
            self.assertEqual(charts.tracks[0].video_id, "daily")
            self.assertEqual(charts.source_title, "Daily Top Music Videos - Germany")

    def test_wrong_playlist_contents_do_not_become_chart_tracks(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"videos": [
                reference("PLweekly", "Weekly Top Music Videos - Germany"),
            ]}
            yt.get_playlist.return_value = {"author": {"id": "FEmusic_charts"}, "title": "My AI music collection", "tracks": [song("wrong")]}
            charts = get_charts("DE")
            self.assertEqual(charts.tracks, [])
            self.assertIsNone(charts.source_url)

    def test_global_order_is_preserved_without_a_country_or_language_filter(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"videos": [reference("PLglobal", "Top 100 Music Videos Global")]}
            yt.get_playlist.return_value = {"author": {"id": "FEmusic_charts"}, "tracks": [song("india", "भारतीय गीत"), song("usa", "US song")]}
            charts = get_charts("ZZ")
            self.assertEqual(charts.country, "ZZ")
            self.assertEqual([track.video_id for track in charts.tracks], ["india", "usa"])

    def test_unrelated_album_radio_and_genre_lists_are_not_fallback_charts(self):
        raw = {"videos": [
            reference("OLAK5uy_album", "Weekly Top Songs - Germany"),
            reference("RDAMradio", "Weekly Top Songs - Germany"),
            reference("PLmix", "A mix for you"),
            reference("PLgenre", "Top 50 Pop Music Videos United States"),
            reference("PLdaily", "Daily Top Music Videos - Germany"),
            reference("PLsongs", "Weekly Top Songs - Germany"),
        ]}
        self.assertEqual([entry["playlistId"] for entry in chart_playlists(raw)], ["PLsongs", "PLdaily"])

    def test_missing_charts_never_use_an_unrelated_working_playlist(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"videos": [reference("PLmix", "Discover music")]}
            self.assertEqual(get_charts("DE").tracks, [])
            yt.get_playlist.assert_not_called()

    def test_weekly_top_100_beats_daily_charts_even_without_weekly_in_title(self):
        entries = chart_playlists({"videos": [
            reference("PLdaily", "Daily Top Music Videos - Germany"),
            reference("PLweekly", "Top 100 Music Videos Germany"),
        ]})
        self.assertEqual(entries[0]["playlistId"], "PLweekly")

    def test_chart_title_alone_does_not_make_a_user_playlist_official(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"videos": [reference("PLfake", "Top 100 Music Videos Germany")]}
            yt.get_playlist.return_value = {
                "title": "Top 100 Music Videos Germany", "author": {"id": "UCunofficial"},
                "tracks": [song("wrong")],
            }
            self.assertEqual(get_charts("DE").tracks, [])


if __name__ == "__main__":
    unittest.main()
