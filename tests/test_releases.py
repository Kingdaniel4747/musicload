"""Release recommendations stay tied to chart artists and the current year."""

from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from musicload.search import get_new_releases


YEAR = datetime.now(UTC).year


def release(browse_id, title="New release", year=YEAR):
    return {"browseId": browse_id, "title": title, "year": str(year), "thumbnails": []}


class ReleaseTests(unittest.TestCase):
    def test_anonymous_explore_recommendations_and_old_or_undated_albums_are_excluded(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"artists": [{"browseId": "UCartist", "title": "Known artist"}]}
            yt.get_artist.return_value = {
                "name": "Known artist", "albums": {"results": [
                    release("old", year=YEAR - 1), release("current"),
                    release("future", year=YEAR + 1), release("undated", year=None),
                ]},
            }
            albums = get_new_releases("DE")
            self.assertEqual([album.browse_id for album in albums], ["current"])
            self.assertEqual(albums[0].artist, "Known artist")
            self.assertEqual(albums[0].year, YEAR)
            yt.get_explore.assert_not_called()
            yt.get_artist.assert_called_once_with("UCartist")
            constructor.assert_any_call(language="en", location="DE")

    def test_artist_catalog_is_sorted_by_recency_instead_of_popularity(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"artists": [{"browseId": "UCartist"}]}
            yt.get_artist.return_value = {
                "name": "Artist", "singles": {
                    "browseId": "MPADartist", "params": "single-params",
                    "results": [release("popular-old", year=YEAR - 1)],
                },
            }
            yt.get_artist_albums.return_value = [release("newest")]
            self.assertEqual(get_new_releases("DE")[0].browse_id, "newest")
            yt.get_artist_albums.assert_called_once_with("MPADartist", "single-params", limit=5, order="Recency")

    def test_one_unavailable_artist_does_not_block_other_releases(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"artists": [{"browseId": "UCbad"}, {"browseId": "UCgood"}]}
            def profile(browse_id):
                if browse_id == "UCbad":
                    raise ValueError("Artist unavailable")
                return {"name": "Good artist", "singles": {"results": [release("good")]}}
            yt.get_artist.side_effect = profile
            self.assertEqual([album.browse_id for album in get_new_releases("US")], ["good"])

    def test_duplicates_are_removed_and_artists_are_interleaved(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"artists": [{"browseId": "UCa"}, {"browseId": "UCa"}, {"browseId": "UCb"}]}
            yt.get_artist.side_effect = lambda browse_id: {
                "name": browse_id,
                "singles": {"results": [release(f"{browse_id}-1", "First"), release(f"{browse_id}-2", "Second")]},
                "albums": {"results": [release(f"{browse_id}-1", "First")]},
            }
            albums = get_new_releases("DE")
            self.assertEqual([album.browse_id for album in albums], ["UCa-1", "UCb-1", "UCa-2", "UCb-2"])
            self.assertEqual(yt.get_artist.call_count, 2)

    def test_global_uses_global_chart_artists_without_an_invalid_location(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"artists": []}
            self.assertEqual(get_new_releases("ZZ"), [])
            constructor.assert_called_once_with(language="en", location="")
            yt.get_charts.assert_called_once_with("ZZ")
            yt.get_explore.assert_not_called()

    def test_artist_requests_are_bounded(self):
        with patch("musicload.search.YTMusic") as constructor:
            yt = constructor.return_value
            yt.get_charts.return_value = {"artists": [{"browseId": f"UC{i}"} for i in range(50)]}
            yt.get_artist.return_value = {"name": "Artist"}
            self.assertEqual(get_new_releases("DE"), [])
            self.assertEqual(yt.get_artist.call_count, 12)


if __name__ == "__main__":
    unittest.main()
