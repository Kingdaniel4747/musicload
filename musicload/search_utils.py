"""Shared parsing and filtering helpers for YouTube Music data."""

VIDEO_TYPE_ATV = "MUSIC_VIDEO_TYPE_ATV"
VIDEO_TYPE_OMV = "MUSIC_VIDEO_TYPE_OMV"
VIDEO_TYPE_UGC = "MUSIC_VIDEO_TYPE_UGC"
VIDEO_TYPE_OFFICIAL_SOURCE = "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC"
ALLOWED_VIDEO_TYPES = frozenset({VIDEO_TYPE_ATV, VIDEO_TYPE_OMV})


def is_allowed_video_type(video_type: str | None, allow_ugc: bool = False) -> bool:
    """Return whether a YouTube Music video type may be included."""
    if video_type is None:
        return False
    if video_type in ALLOWED_VIDEO_TYPES:
        return True
    return allow_ugc and video_type in (VIDEO_TYPE_UGC, VIDEO_TYPE_OFFICIAL_SOURCE)


def parse_duration(duration_text: str) -> int:
    """Parse a duration such as ``3:45`` or ``1:03:45`` into seconds."""
    try:
        parts = [int(part) for part in duration_text.split(":")]
    except ValueError:
        return 0
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    return 0
