"""Configuration handling for Musicload."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from musicload.settings import load_settings

logger = logging.getLogger(__name__)

# Default filename template: Artist - Title
DEFAULT_FILENAME_TEMPLATE = "%(artist,uploader)s - %(title)s"

# Maximum filename length in bytes (excluding extension).
# Most filesystems (ext4, btrfs, NTFS) limit filenames to 255 bytes.
# We use 200 to leave room for the extension and intermediate files like .webp thumbnails.
MAX_FILENAME_BYTES = 200


class _SettingValues:
    """Read settings.json values before environment variables and defaults."""

    def __init__(self, web_settings: dict):
        self.web_settings = web_settings

    def get(self, name: str, env_name: str, default=None):
        if name in self.web_settings:
            return self.web_settings[name]
        return os.getenv(env_name, default)

    def boolean(self, name: str, env_name: str, default: bool = False) -> bool:
        raw = self.get(name, env_name, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("true", "1", "yes", "on")

    def optional_text(self, name: str, env_name: str) -> str | None:
        raw = self.get(name, env_name)
        if raw is None:
            return None
        return str(raw).strip() or None


def _parse_cors_origins(raw: object) -> list[str]:
    if isinstance(raw, list):
        origins = [str(origin).strip() for origin in raw if str(origin).strip()]
    elif str(raw).strip() == "*":
        origins = ["*"]
    else:
        origins = [origin.strip() for origin in str(raw).split(",") if origin.strip()]
    return origins or ["*"]


def _parse_cookie_mode(raw: object) -> str:
    mode = str(raw).lower()
    if mode in ("auto", "always", "never"):
        return mode
    logger.warning(
        "Invalid MUSICLOAD_COOKIE_MODE '%s', falling back to 'auto'",
        mode,
    )
    return "auto"


@dataclass
class Config:
    """Application configuration."""

    download_dir: Path
    data_dir: Path
    audio_format: str
    filename_template: str
    organization_mode: str
    use_primary_artist: bool
    web_port: int
    web_playlist_name: str | None = None
    gotify_url: str | None = None
    gotify_token: str | None = None
    yt_dlp_cookie_file: str | None = None
    cookie_mode: str = "auto"
    cookie_retry_delay: float = 1.0
    log_cookie_usage: bool = True
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    unavailable_cooldown_hours: int = 168  # 7 days
    lyrics_cache_hours: int = 168  # 7 days negative TTL
    multi_user: bool = False
    replaygain: bool = False
    allow_ugc: bool = False
    navidrome_url: str | None = None
    session_secret: str | None = None
    session_https_only: bool = True
    listenbrainz_web: bool = False

    def effective_playlist_name(self, remote_user: str | None) -> str | None:
        """Return the playlist name, optionally prefixed with the remote user."""
        if not self.web_playlist_name:
            return None
        if not self.multi_user or not remote_user:
            return self.web_playlist_name
        return f"{remote_user}-{self.web_playlist_name}"

    @property
    def cookie_file_path(self) -> str | None:
        """Get the effective cookie file path, checking uploaded file first."""
        # Check for uploaded cookie file in data_dir
        uploaded_cookie = self.data_dir / "cookies.txt"
        if uploaded_cookie.exists():
            return str(uploaded_cookie)

        # Fall back to env var
        return self.yt_dlp_cookie_file

    @classmethod
    def from_env(cls) -> "Config":
        """Create config from web overrides, environment variables, and defaults."""

        # MUSICLOAD_DATA_DIR is the one bootstrap setting that cannot live in
        # settings.json because it tells Musicload where that file is stored.
        configured_data_dir = os.getenv("MUSICLOAD_DATA_DIR")
        data_dir = (
            Path(configured_data_dir).expanduser()
            if configured_data_dir
            else Path.home() / ".musicload"
        )
        data_dir.mkdir(parents=True, exist_ok=True)
        values = _SettingValues(load_settings(data_dir))

        # Parse CORS origins
        cors_origins = _parse_cors_origins(
            values.get("cors_origins", "MUSICLOAD_CORS_ORIGINS", "*")
        )

        # Parse and validate cookie mode
        cookie_mode = _parse_cookie_mode(
            values.get("cookie_mode", "MUSICLOAD_COOKIE_MODE", "auto")
        )

        # Parse cookie retry delay
        cookie_retry_delay = float(
            values.get("cookie_retry_delay", "MUSICLOAD_COOKIE_RETRY_DELAY", 1.0)
        )
        if cookie_retry_delay < 0:
            raise ValueError(
                f"MUSICLOAD_COOKIE_RETRY_DELAY must be non-negative, got {cookie_retry_delay}"
            )

        # Parse log cookie usage flag
        log_cookie_usage = values.boolean(
            "log_cookie_usage", "MUSICLOAD_LOG_COOKIE_USAGE", True
        )

        # Parse unavailable cooldown hours (0 = disabled)
        unavailable_cooldown_hours = int(
            values.get(
                "unavailable_cooldown_hours",
                "MUSICLOAD_UNAVAILABLE_COOLDOWN_HOURS",
                168,
            )
        )
        if unavailable_cooldown_hours < 0:
            logger.warning(
                "MUSICLOAD_UNAVAILABLE_COOLDOWN_HOURS is negative (%d), using 0 (disabled)",
                unavailable_cooldown_hours
            )
            unavailable_cooldown_hours = 0

        # Parse lyrics cache TTL hours (0 = negatives never expire)
        lyrics_cache_hours = int(
            values.get("lyrics_cache_hours", "MUSICLOAD_LYRICS_CACHE_HOURS", 168)
        )
        if lyrics_cache_hours < 0:
            logger.warning(
                "MUSICLOAD_LYRICS_CACHE_HOURS is negative (%d), using 0 (no expiry)",
                lyrics_cache_hours
            )
            lyrics_cache_hours = 0

        # Parse multi-user mode flag
        multi_user = values.boolean("multi_user", "MUSICLOAD_MULTI_USER")

        # Parse replaygain flag
        replaygain = values.boolean("replaygain", "MUSICLOAD_REPLAYGAIN")

        # Parse allow_ugc flag
        allow_ugc = values.boolean("allow_ugc", "MUSICLOAD_ALLOW_UGC")

        # Parse web port
        web_port = int(values.get("web_port", "MUSICLOAD_WEB_PORT", 8000))
        if not (1 <= web_port <= 65535):
            raise ValueError(
                f"MUSICLOAD_WEB_PORT must be between 1 and 65535, got {web_port}"
            )

        download_dir = Path(
            str(values.get("download_dir", "MUSICLOAD_DOWNLOAD_DIR", "./downloads"))
        ).expanduser()

        return cls(
            download_dir=download_dir,
            data_dir=data_dir,
            audio_format=str(
                values.get("audio_format", "MUSICLOAD_AUDIO_FORMAT", "opus")
            ),
            filename_template=str(
                values.get(
                    "filename_template",
                    "MUSICLOAD_FILENAME_TEMPLATE",
                    DEFAULT_FILENAME_TEMPLATE,
                )
            ),
            organization_mode=str(
                values.get(
                    "organization_mode", "MUSICLOAD_ORGANIZATION_MODE", "album"
                )
            ),
            use_primary_artist=values.boolean(
                "use_primary_artist", "MUSICLOAD_USE_PRIMARY_ARTIST"
            ),
            web_port=web_port,
            web_playlist_name=values.optional_text(
                "web_playlist_name", "MUSICLOAD_WEB_PLAYLIST"
            ),
            gotify_url=values.optional_text("gotify_url", "GOTIFY_URL"),
            gotify_token=values.optional_text("gotify_token", "GOTIFY_TOKEN"),
            yt_dlp_cookie_file=os.getenv("YT_DLP_COOKIE_FILE"),
            cookie_mode=cookie_mode,
            cookie_retry_delay=cookie_retry_delay,
            log_cookie_usage=log_cookie_usage,
            cors_origins=cors_origins,
            unavailable_cooldown_hours=unavailable_cooldown_hours,
            lyrics_cache_hours=lyrics_cache_hours,
            multi_user=multi_user,
            replaygain=replaygain,
            allow_ugc=allow_ugc,
            navidrome_url=(
                values.optional_text("navidrome_url", "NAVIDROME_URL") or ""
            ).rstrip("/")
            or None,
            session_secret=values.optional_text(
                "session_secret", "MUSICLOAD_SESSION_SECRET"
            ),
            session_https_only=values.boolean(
                "session_https_only", "MUSICLOAD_SESSION_HTTPS_ONLY", True
            ),
            listenbrainz_web=values.boolean(
                "listenbrainz_web", "MUSICLOAD_LISTENBRAINZ_WEB"
            ),
        )

    @property
    def gotify_configured(self) -> bool:
        """Check if Gotify notifications are configured."""
        return bool(self.gotify_url and self.gotify_token)

    def validate_organization_mode(self):
        """Validate organization mode value."""
        if self.organization_mode not in ("flat", "album"):
            raise ValueError(
                f"Invalid organization mode: {self.organization_mode}. Must be 'flat' or 'album'."
            )


def get_config() -> Config:
    """Get the current configuration."""
    return Config.from_env()
