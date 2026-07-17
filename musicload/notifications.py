"""Gotify notification support for Musicload."""

import logging
from typing import Optional

import httpx

from musicload.config import get_config

logger = logging.getLogger(__name__)


class GotifyNotifier:
    """Handles Gotify push notifications."""

    def __init__(self, url: str, token: str):
        """
        Initialize Gotify notifier.

        Args:
            url: Gotify server URL
            token: Application token
        """
        self.url = url.rstrip("/")
        self.token = token
        self.endpoint = f"{self.url}/message"

    def send(
        self,
        title: str,
        message: str,
        priority: int = 5,
    ) -> bool:
        """
        Send notification to Gotify.

        Args:
            title: Notification title
            message: Notification message
            priority: Priority level (0-10)

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    self.endpoint,
                    params={"token": self.token},
                    json={
                        "title": title,
                        "message": message,
                        "priority": priority,
                    },
                )
                response.raise_for_status()
                logger.debug("Gotify notification sent: %s", title)
                return True

        except httpx.HTTPError as e:
            logger.warning("Failed to send Gotify notification: %s", e)
            return False
        except Exception as e:
            logger.warning("Unexpected error sending Gotify notification: %s", e)
            return False


def _get_notifier() -> Optional[GotifyNotifier]:
    """Get configured Gotify notifier or None if not configured."""
    config = get_config()

    if not config.gotify_configured:
        return None

    return GotifyNotifier(config.gotify_url, config.gotify_token)


def send_sync_notification(
    name: str,
    sync_type: str,
    result: Optional[object],
    success: bool,
    error: Optional[str] = None,
) -> None:
    """
    Send notification for playlist/plugin sync operation.

    Args:
        name: Playlist or plugin name
        sync_type: Type of sync ("playlist" or "plugin")
        result: Sync result with counts (None if failed)
        success: Whether sync succeeded
        error: Error message if failed
    """
    notifier = _get_notifier()
    if not notifier:
        return

    if not success:
        notifier.send(
            title=f"❌ {sync_type.title()} Sync Failed: {name}",
            message=f"Error: {error}\nType: {sync_type}",
            priority=8,
        )
        return

    lines = ["✅ Completed successfully"]

    if sync_type == "plugin" and hasattr(result, "plugin_name"):
        lines.append(f"• Type: {sync_type}")

    lines.extend([
        f"• Downloaded: {result.downloaded}",
        f"• Skipped: {result.skipped}",
    ])

    if hasattr(result, "deleted") and result.deleted > 0:
        lines.append(f"• Deleted: {result.deleted}")

    lines.append(f"• Failed: {result.failed}")

    message = "\n".join(lines)

    priority = 6 if result.failed > 0 else 5

    notifier.send(
        title=f"{sync_type.title()} Sync: {name}",
        message=message,
        priority=priority,
    )
