"""Shared yt-dlp options for YouTube extraction and downloads."""

from typing import Any


def youtube_ydl_options() -> dict[str, Any]:
    """Return options required for current YouTube challenge handling.

    Player clients are deliberately not pinned. yt-dlp updates its default
    client selection as YouTube changes token requirements; an old fixed list
    was the reason Musicload kept selecting unusable ``ANDROID_VR`` URLs.
    """
    return {
        # The runtime image contains Deno. yt-dlp[default] also ships the
        # matching EJS scripts; the remote source is an update-compatible
        # fallback when YouTube changes its JavaScript challenge.
        "remote_components": ["ejs:github"],
    }
