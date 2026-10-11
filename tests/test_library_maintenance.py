"""Offline coverage for destructive cleanup and fuzzy duplicate candidates."""
import tempfile
import unittest
from pathlib import Path

from musicload.download_cleanup import delete_track_files
from musicload.library_maintenance import duplicate_groups, scan_music_free_folders, delete_music_free_folder


class LibraryMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, name, content=b"test"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_last_track_removes_lyrics_cover_and_empty_ancestors(self):
        song = self.file("Artist/Album/Song.mp3")
        self.file("Artist/Album/Song.lrc")
        self.file("Artist/Album/cover.jpg")
        (song.parent / "empty" / "nested").mkdir(parents=True)
        delete_track_files(song, self.root, {".mp3"})
        self.assertTrue(self.root.exists())
        self.assertFalse((self.root / "Artist").exists())

    def test_other_song_and_shared_cover_are_preserved(self):
        song = self.file("Artist/Album/First.mp3")
        self.file("Artist/Album/First.lrc")
        other = self.file("Artist/Album/Second.flac")
        lyrics = self.file("Artist/Album/Second.lrc")
        cover = self.file("Artist/Album/cover.jpg")
        delete_track_files(song, self.root, {".mp3", ".flac"})
        self.assertTrue(other.exists() and lyrics.exists() and cover.exists())
        self.assertFalse(song.with_suffix(".lrc").exists())

    def test_unknown_file_and_sibling_album_survive(self):
        song = self.file("Artist/Album/Song.mp3")
        note = self.file("Artist/Album/notes.pdf")
        sibling = self.file("Artist/Other/Song.mp3")
        delete_track_files(song, self.root, {".mp3"})
        self.assertTrue(note.exists() and sibling.exists())

    def test_shared_stem_sidecar_survives_other_format(self):
        song = self.file("Album/Song.mp3")
        self.file("Album/Song.flac")
        lyrics = self.file("Album/Song.lrc")
        delete_track_files(song, self.root, {".mp3", ".flac"})
        self.assertTrue(lyrics.exists())

    def test_scan_and_manual_cleanup_preserve_music_and_unknown_files(self):
        self.file("Artist/Empty/cover.jpg")
        self.file("Artist/Empty/nested/old.lrc")
        song = self.file("Artist/Music/Song.m4a")
        unknown = self.file("Other/readme.pdf")
        result = scan_music_free_folders(self.root)
        found = {Path(f["entry_path"]).as_posix(): f for f in result["folders"]}
        self.assertTrue(found["Artist/Empty"]["can_delete"])
        self.assertFalse(found["Other"]["can_delete"])
        self.assertTrue((self.root / "Artist/Empty").exists())
        delete_music_free_folder(self.root, "Artist/Empty")
        self.assertTrue(song.exists() and unknown.exists())
        with self.assertRaises(ValueError):
            delete_music_free_folder(self.root, "Other")

    def test_changed_folder_revalidated_before_deletion(self):
        self.file("Empty/cover.jpg")
        scan_music_free_folders(self.root)
        song = self.file("Empty/New.mp3")
        with self.assertRaises(ValueError):
            delete_music_free_folder(self.root, "Empty")
        self.assertTrue(song.exists() and song.with_name("cover.jpg").exists())
        for value in (".", "../outside"):
            with self.assertRaises(ValueError):
                delete_music_free_folder(self.root, value)

    def records(self, entries):
        records = []
        for path, title, artist, content in entries:
            file = self.file(path, content)
            records.append({"entry_path": path, "title": title, "artist": artist,
                            "duration": "3:20", "file_size": file.stat().st_size})
        return records

    def test_similar_names_different_folders_and_formats(self):
        records = self.records([
            ("a/01.mp3", "01 - Beyoncé - Beautiful Song (Official Audio)", "Beyonce", b"a"),
            ("b/other.flac", "Beautifull Song (Remastered 2024)", "Beyoncé", b"bb"),
            ("c/copy.opus", "Song Beautiful (2)", "Beyonce", b"ccc"),
            ("d/other.mp3", "Beautifull Song", "Different Artist", b"dddd"),
        ])
        groups = duplicate_groups(records, self.root)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["tracks"]), 3)

    def test_identical_bytes_detected_despite_unrelated_names(self):
        records = self.records([("a/One.mp3", "One", "A", b"same"),
                                ("b/Two.mp3", "Two", "B", b"same")])
        self.assertEqual(duplicate_groups(records, self.root)[0]["kind"], "identical")

    def test_identical_bytes_keep_exact_label_for_matching_names(self):
        records = self.records([("a/One.mp3", "One", "A", b"same"),
                                ("b/Copy.mp3", "One", "A", b"same")])
        self.assertEqual(duplicate_groups(records, self.root)[0]["kind"], "identical")

    def test_distinct_versions_are_not_fuzzy_merged(self):
        records = self.records([("a.mp3", "Beautiful Song", "A", b"one"),
                                ("b.mp3", "Beautiful Song live", "A", b"twotwo")])
        self.assertFalse(duplicate_groups(records, self.root))
