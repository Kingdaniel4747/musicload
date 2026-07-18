"""Configuration handling for Musicload."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default filename template: Artist - Title
DEFAULT_FILENAME_TEMPLATE = "%(artist,uploader)s - %(title)s"

# Maximum filename length in bytes (excluding extension).
# Most filesystems (ext4, btrfs, NTFS) limit filenames to 255 bytes.
# We use 200 to leave room for the extension and intermediate files like .webp thumbnails.
MAX_FILENAME_BYTES = 200


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
        """Create config from environment variables with defaults."""
        # Parse CORS origins
        cors_env = os.getenv("MUSICLOAD_CORS_ORIGINS", "*")
        if cors_env == "*":
            cors_origins = ["*"]
        else:
            cors_origins = [origin.strip() for origin in cors_env.split(",")]

        # Parse and validate cookie mode
        cookie_mode = os.getenv("MUSICLOAD_COOKIE_MODE", "auto").lower()
        if cookie_mode not in ("auto", "always", "never"):
            logger.warning(
                "Invalid MUSICLOAD_COOKIE_MODE '%s', falling back to 'auto'",
                cookie_mode
            )
            cookie_mode = "auto"

        # Parse cookie retry delay
        cookie_retry_delay = float(os.getenv("MUSICLOAD_COOKIE_RETRY_DELAY", "1.0"))
        if cookie_retry_delay < 0:
            raise ValueError(
                f"MUSICLOAD_COOKIE_RETRY_DELAY must be non-negative, got {cookie_retry_delay}"
            )

        # Parse log cookie usage flag
        log_cookie_usage = os.getenv("MUSICLOAD_LOG_COOKIE_USAGE", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        # Parse unavailable cooldown hours (0 = disabled)
        unavailable_cooldown_hours = int(os.getenv("MUSICLOAD_UNAVAILABLE_COOLDOWN_HOURS", "168"))
        if unavailable_cooldown_hours < 0:
            logger.warning(
                "MUSICLOAD_UNAVAILABLE_COOLDOWN_HOURS is negative (%d), using 0 (disabled)",
                unavailable_cooldown_hours
            )
            unavailable_cooldown_hours = 0

        # Parse lyrics cache TTL hours (0 = negatives never expire)
        lyrics_cache_hours = int(os.getenv("MUSICLOAD_LYRICS_CACHE_HOURS", "168"))
        if lyrics_cache_hours < 0:
            logger.warning(
                "MUSICLOAD_LYRICS_CACHE_HOURS is negative (%d), using 0 (no expiry)",
                lyrics_cache_hours
            )
            lyrics_cache_hours = 0

        # Parse multi-user mode flag
        multi_user = os.getenv("MUSICLOAD_MULTI_USER", "false").lower() in ("true", "1", "yes")

        # Parse replaygain flag
        replaygain = os.getenv("MUSICLOAD_REPLAYGAIN", "false").lower() in ("true", "1", "yes")

        # Parse allow_ugc flag
        allow_ugc = os.getenv("MUSICLOAD_ALLOW_UGC", "false").lower() in ("true", "1", "yes")

        # Parse web port
        web_port = int(os.getenv("MUSICLOAD_WEB_PORT", "8000"))
        if not (1 <= web_port <= 65535):
            raise ValueError(
                f"MUSICLOAD_WEB_PORT must be between 1 and 65535, got {web_port}"
            )

        # Parse download directory first so data_dir can default under it
        download_dir = Path(os.getenv("MUSICLOAD_DOWNLOAD_DIR", "./downloads"))

        # Parse data directory:
        # - explicit MUSICLOAD_DATA_DIR wins
        # - default keeps legacy location under download_dir to avoid upgrade breakage
        data_dir = Path(os.getenv("MUSICLOAD_DATA_DIR", str(download_dir / ".musicload")))
        data_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            download_dir=download_dir,
            data_dir=data_dir,
            audio_format=os.getenv("MUSICLOAD_AUDIO_FORMAT", "opus"),
            filename_template=os.getenv("MUSICLOAD_FILENAME_TEMPLATE", DEFAULT_FILENAME_TEMPLATE),
            organization_mode=os.getenv("MUSICLOAD_ORGANIZATION_MODE", "flat"),
            use_primary_artist=os.getenv("MUSICLOAD_USE_PRIMARY_ARTIST", "false").lower() in ("true", "1", "yes"),
            web_port=web_port,
            web_playlist_name=os.getenv("MUSICLOAD_WEB_PLAYLIST"),
            gotify_url=os.getenv("GOTIFY_URL"),
            gotify_token=os.getenv("GOTIFY_TOKEN"),
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
