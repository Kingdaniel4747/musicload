"""Validation and serialization for web-managed application settings."""

from urllib.parse import urlparse

from fastapi import HTTPException

from musicload.config import get_config
from musicload.settings import RESTART_REQUIRED_SETTINGS, load_settings
from musicload.web.schemas import AppSettingsRequest


def clean_optional_setting(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _validate_choice(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid {label}")


def _validate_http_url(label: str, url: str | None) -> None:
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{label} must be an HTTP(S) URL")


def _validate_playlist_name(playlist_name: str | None) -> None:
    if playlist_name and (
        playlist_name in {".", ".."}
        or "/" in playlist_name
        or "\\" in playlist_name
    ):
        raise HTTPException(
            status_code=400, detail="Playlist name must not contain a path"
        )


def _preserve_secret(
    values: dict,
    key: str,
    new_value: str | None,
    clear: bool,
    existing_overrides: dict,
) -> None:
    if clear:
        values[key] = None
    elif new_value is not None:
        values[key] = new_value
    elif key in existing_overrides:
        values[key] = existing_overrides[key]


def build_settings_values(
    payload: AppSettingsRequest, effective, existing_overrides: dict
) -> dict:
    """Validate one settings request and return values safe to persist."""
    _validate_choice(payload.audio_format, {"opus", "mp3", "flac"}, "audio format")
    _validate_choice(
        payload.organization_mode, {"flat", "album"}, "organization mode"
    )
    _validate_choice(payload.cookie_mode, {"auto", "always", "never"}, "cookie mode")

    navidrome_url = clean_optional_setting(payload.navidrome_url)
    gotify_url = clean_optional_setting(payload.gotify_url)
    _validate_http_url("Navidrome URL", navidrome_url)
    _validate_http_url("Gotify URL", gotify_url)

    playlist_name = clean_optional_setting(payload.web_playlist_name)
    _validate_playlist_name(playlist_name)
    filename_template = payload.filename_template.strip()
    if not filename_template:
        raise HTTPException(status_code=400, detail="Filename template is required")

    new_session_secret = clean_optional_setting(payload.session_secret)
    candidate_session_secret = (
        None
        if payload.clear_session_secret
        else new_session_secret or effective.session_secret
    )
    if navidrome_url and (
        not candidate_session_secret or len(candidate_session_secret) < 32
    ):
        raise HTTPException(
            status_code=400,
            detail="Navidrome login requires a session secret with at least 32 characters",
        )

    values = {
        "audio_format": payload.audio_format,
        "filename_template": filename_template,
        "organization_mode": payload.organization_mode,
        "use_primary_artist": payload.use_primary_artist,
        "web_playlist_name": playlist_name,
        "gotify_url": gotify_url,
        "cookie_mode": payload.cookie_mode,
        "multi_user": payload.multi_user,
        "allow_ugc": payload.allow_ugc,
        "navidrome_url": navidrome_url,
        "session_https_only": payload.session_https_only,
        "listenbrainz_web": payload.listenbrainz_web,
    }
    _preserve_secret(
        values,
        "session_secret",
        new_session_secret,
        payload.clear_session_secret,
        existing_overrides,
    )
    _preserve_secret(
        values,
        "gotify_token",
        clean_optional_setting(payload.gotify_token),
        payload.clear_gotify_token,
        existing_overrides,
    )
    return values


def settings_response() -> dict:
    """Return effective values while redacting stored secrets."""
    effective = get_config()
    overrides = load_settings(effective.data_dir)
    values = {
        "audio_format": effective.audio_format,
        "filename_template": effective.filename_template,
        "organization_mode": effective.organization_mode,
        "use_primary_artist": effective.use_primary_artist,
        "web_playlist_name": effective.web_playlist_name or "",
        "gotify_url": effective.gotify_url or "",
        "cookie_mode": effective.cookie_mode,
        "multi_user": effective.multi_user,
        "allow_ugc": effective.allow_ugc,
        "navidrome_url": effective.navidrome_url or "",
        "session_https_only": effective.session_https_only,
        "listenbrainz_web": effective.listenbrainz_web,
    }
    return {
        "values": values,
        "configured": {
            "gotify_token": bool(effective.gotify_token),
            "session_secret": bool(effective.session_secret),
        },
        "overridden": sorted(overrides),
        "restart_required_fields": sorted(RESTART_REQUIRED_SETTINGS),
    }
