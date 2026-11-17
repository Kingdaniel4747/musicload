"""Choose explicit chart playlists from YouTube Music's charts response."""

import re


def chart_priority(title: str) -> int | None:
    """Prefer weekly/Top-100 charts over daily charts; reject other lists.

    The caller requests English metadata so playlist titles can be classified
    consistently without guessing from translated genre or recommendation lists.
    """
    normalized = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    match = re.match(r"^(?:(weekly|daily) )?top (?:\d+ )?(songs|music videos)(?: |$)", normalized)
    if not match:
        return None
    # YouTube's weekly lists are also titled "Top 100 Music Videos Germany"
    # without a "Weekly" prefix. Keep those ahead of daily charts.
    period = {"weekly": 0, "daily": 2, None: 1}[match[1]]
    return period * 2 + (match[2] == "music videos")


def chart_playlists(raw: dict) -> list[dict]:
    """Return named charts only, preserving upstream order within each type."""
    entries = raw.get("videos", [])
    if not isinstance(entries, list):
        return []
    candidates = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        playlist_id = entry.get("playlistId")
        title = entry.get("title")
        if not isinstance(playlist_id, str) or not re.fullmatch(r"PL[A-Za-z0-9_-]+", playlist_id):
            continue
        if not isinstance(title, str):
            continue
        priority = chart_priority(title)
        if priority is None or playlist_id in seen:
            continue
        seen.add(playlist_id)
        candidates.append((priority, entry))
    candidates.sort(key=lambda candidate: candidate[0])
    return [entry for _, entry in candidates]
