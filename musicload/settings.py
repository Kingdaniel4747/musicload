"""Persistent application settings managed by the web interface.

Environment variables remain the bootstrap/fallback configuration. Values
stored in ``settings.json`` take precedence so a container can be configured
after its first start without editing Compose for every application option.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SETTINGS_FILENAME = "settings.json"

# These fields are never returned verbatim by the settings API.
SENSITIVE_SETTINGS = frozenset({"gotify_token", "session_secret"})

# These values are consumed while the web server or middleware is created.
RESTART_REQUIRED_SETTINGS = frozenset(
    {
        "cors_origins",
        "listenbrainz_web",
        "navidrome_url",
        "session_https_only",
        "session_secret",
        "web_port",
    }
)


def settings_path(data_dir: Path) -> Path:
    """Return the persistent settings path without changing configuration."""
    return data_dir / SETTINGS_FILENAME


def load_settings(data_dir: Path) -> dict[str, Any]:
    """Load web-managed overrides, ignoring malformed files safely."""
    path = settings_path(data_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_settings(data_dir: Path, values: dict[str, Any]) -> Path:
    """Atomically persist validated web settings with private permissions."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = settings_path(data_dir)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(values, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return path


def clear_settings(data_dir: Path) -> bool:
    """Delete all web overrides and fall back to environment/default values."""
    path = settings_path(data_dir)
    if not path.exists():
        return False
    path.unlink()
    return True
