"""Formatting and callback helpers for yt-dlp progress updates."""

from collections.abc import Callable


def _format_speed(speed: float | None) -> str:
    if not speed:
        return "N/A"
    if speed > 1024 * 1024:
        return f"{speed / 1024 / 1024:.1f} MB/s"
    if speed > 1024:
        return f"{speed / 1024:.1f} KB/s"
    return f"{speed:.0f} B/s"


def _format_eta(eta: float | None) -> str:
    if not eta:
        return "N/A"
    seconds = int(eta)
    if eta > 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if eta > 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def make_progress_hook(callback: Callable[[dict], None]) -> Callable[[dict], None]:
    """Adapt a yt-dlp progress event to Musicload's compact progress payload."""

    def progress_hook(data: dict) -> None:
        if data["status"] != "downloading":
            return
        downloaded = data.get("downloaded_bytes", 0)
        total = data.get("total_bytes") or data.get("total_bytes_estimate", 0)
        callback(
            {
                "downloaded_bytes": downloaded,
                "total_bytes": total,
                "percent": (downloaded / total * 100) if total > 0 else 0,
                "speed": _format_speed(data.get("speed", 0)),
                "eta": _format_eta(data.get("eta", 0)),
            }
        )

    return progress_hook
