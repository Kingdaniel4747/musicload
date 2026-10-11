"""Explicit library scans; no background scans or automatic deletion."""
import hashlib
import os
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from musicload.download_cleanup import DOWNLOAD_SIDECAR_EXTENSIONS, PRESERVED_AUDIO_EXTENSIONS
from musicload.tagging import SUPPORTED_EXTENSIONS

AUDIO_EXTENSIONS = SUPPORTED_EXTENSIONS | PRESERVED_AUDIO_EXTENSIONS
NOISE = {"official", "audio", "video", "music", "lyrics", "lyric", "copy", "kopie",
         "hd", "hq", "uhd", "remaster", "remastered", "version", "original"}


def normalise(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    return re.sub(r"[^\w]+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


def title_key(value: str, artist: str = "") -> str:
    value = re.sub(r"^\s*\d{1,3}\s*[-_.]\s*", "", value)
    value = re.sub(r"\s*[\[(]\d+[\])]\s*$", "", value)
    value = re.sub(r"\s*[\[(][A-Za-z0-9_-]{11}[\])]\s*$", "", value)
    words = normalise(value).split()
    artist_words = normalise(artist).split()
    if artist_words and words[:len(artist_words)] == artist_words:
        words = words[len(artist_words):]
    words = [w for w in words if w not in NOISE and not (w.isdecimal() and len(w) == 4 and 1900 <= int(w) <= 2099)]
    return " ".join(sorted(words))


def duplicate_groups(records: list[dict], root: Path) -> list[dict]:
    """Group byte-identical files and likely title matches across directories.

    Trigram indexing limits fuzzy comparisons; duration and artist guard
    against merging unrelated near matches. Results always require review.
    """
    parents = list(range(len(records)))
    reasons = {}
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    def join(i, j, reason):
        a, b = find(i), find(j)
        if a != b:
            parents[b] = a
        if reasons.get((i, j)) != "identical":
            reasons[i, j] = reason

    sizes = defaultdict(list)
    for i, track in enumerate(records):
        sizes[track["file_size"]].append(i)
    for indices in sizes.values():
        if len(indices) < 2:
            continue
        hashes = {}
        for i in indices:
            with (root / records[i]["entry_path"]).open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest in hashes:
                join(hashes[digest], i, "identical")
            else:
                hashes[digest] = i

    titles = [title_key(t["title"], t.get("artist", "")) for t in records]
    artists = [normalise(t.get("artist", "")) for t in records]
    exact, grams = defaultdict(list), defaultdict(set)
    def duration(value):
        try:
            parts = str(value).split(":")
            result = 0.0
            for part in parts:
                result = result * 60 + float(part)
            return result
        except (ValueError, TypeError):
            return 0
    for i, title in enumerate(titles):
        if not title:
            continue
        for j in exact[title]:
            if not artists[i] or not artists[j] or SequenceMatcher(None, artists[i], artists[j]).ratio() >= .8:
                join(j, i, "name")
        exact[title].append(i)
        trigrams = {title[k:k+3] for k in range(max(0, len(title)-2))}
        candidates = set().union(*(grams[g] for g in trigrams)) if trigrams else set()
        for j in candidates:
            if title == titles[j] or min(len(title), len(titles[j])) < 5:
                continue
            if artists[i] and artists[j] and SequenceMatcher(None, artists[i], artists[j]).ratio() < .8:
                continue
            a, b = duration(records[i].get("duration")), duration(records[j].get("duration"))
            if a and b and abs(a-b) > max(8, min(a, b)*.05):
                continue
            if SequenceMatcher(None, title, titles[j]).ratio() >= .86:
                join(j, i, "similar")
        for gram in trigrams:
            grams[gram].add(i)
    groups = defaultdict(list)
    for i, record in enumerate(records):
        groups[find(i)].append(record)
    result = []
    for leader, tracks in groups.items():
        if len(tracks) < 2:
            continue
        kinds = {reason for (i, j), reason in reasons.items() if find(i) == leader}
        kind = "identical" if kinds == {"identical"} else "name"
        result.append({"kind": kind, "label": "Identical files" if kind == "identical" else "Similar song names",
                       "confidence": "exact" if kind == "identical" else "possible",
                       "key": titles[leader], "tracks": tracks})
    return sorted(result, key=lambda g: g["key"])


def scan_music_free_folders(root: Path) -> dict:
    """Return highest music-free subtrees, without following directory links."""
    root = root.resolve()
    states, candidates = {}, []
    for directory, subdirs, files in os.walk(root, topdown=False, followlinks=False):
        path = Path(directory)
        linked = any((path / name).is_symlink() for name in subdirs + files)
        children = [states[path / name] for name in subdirs if path / name in states]
        music = any(Path(name).suffix.lower() in AUDIO_EXTENSIONS for name in files) or any(c[0] for c in children)
        safe = not linked and all(Path(name).suffix.lower() in DOWNLOAD_SIDECAR_EXTENSIONS for name in files) and all(c[1] for c in children)
        count = len(files) + sum(c[2] for c in children)
        states[path] = (music, safe, count)
    for path, (music, safe, count) in states.items():
        if path == root or music or (path.parent != root and not states[path.parent][0]):
            continue
        candidates.append({"entry_path": str(path.relative_to(root)), "file_count": count, "can_delete": safe})
    return {"folders": sorted(candidates, key=lambda d: d["entry_path"]), "total": len(candidates)}


def delete_music_free_folder(root: Path, entry_path: str) -> None:
    """Recheck before removing only empty folders and recognised sidecars."""
    root = root.resolve()
    target = root / entry_path
    resolved = target.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("Invalid folder path")
    if any(p.is_symlink() for p in [target, *target.parents] if p != root and p.is_relative_to(root)):
        raise ValueError("Linked folders cannot be deleted")
    if not target.is_dir():
        raise FileNotFoundError(entry_path)
    # Full validation before touching anything, including nested links/files.
    contents = list(target.rglob("*"))
    for path in contents:
        if path.is_symlink() or (path.is_file() and path.suffix.lower() not in DOWNLOAD_SIDECAR_EXTENSIONS):
            raise ValueError("Folder contains music, unknown files or links; scan again")
    for path in sorted(contents, key=lambda p: len(p.parts), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    target.rmdir()
    parent = target.parent
    while parent != root and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
