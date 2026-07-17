"""Generic hook system for executing commands on events.

Hooks allow users to run custom commands when certain events occur,
such as when a playlist is updated or a sync completes. This enables
integration with external systems like Navidrome playlist import.

Supported events:
- playlist_updated: Triggered when an M3U playlist file is created/updated
- sync_completed: Triggered after a playlist/plugin sync completes

Environment variables available to hook commands:
- MUSICLOAD_EVENT: The event type (e.g., "playlist_updated")
- MUSICLOAD_PLAYLIST_NAME: Name of the playlist/plugin
- MUSICLOAD_PLAYLIST_PATH: Absolute path to the M3U file
- MUSICLOAD_SYNC_TYPE: Type of sync ("playlist" or "plugin")
- MUSICLOAD_DOWNLOADED: Number of tracks downloaded
- MUSICLOAD_SKIPPED: Number of tracks skipped
- MUSICLOAD_DELETED: Number of tracks deleted
- MUSICLOAD_FAILED: Number of tracks that failed
"""

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HookConfig:
    """Configuration for a hook."""

    event: str
    command: str
    timeout: int = 60  # Default timeout in seconds
    run_on_error: bool = False  # Whether to run if sync had errors


@dataclass
class HookContext:
    """Context passed to hooks with event-specific data."""

    event: str
    playlist_name: str | None = None
    playlist_path: Path | None = None
    sync_type: str | None = None  # "playlist" or "plugin"
    downloaded: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: int = 0
    success: bool = True
    extra: dict[str, Any] | None = None

    def to_env(self) -> dict[str, str]:
        """Convert context to environment variables for hook command."""
        env = os.environ.copy()
        env["MUSICLOAD_EVENT"] = self.event

        if self.playlist_name:
            env["MUSICLOAD_PLAYLIST_NAME"] = self.playlist_name
        if self.playlist_path:
            env["MUSICLOAD_PLAYLIST_PATH"] = str(self.playlist_path.absolute())
        if self.sync_type:
            env["MUSICLOAD_SYNC_TYPE"] = self.sync_type

        env["MUSICLOAD_DOWNLOADED"] = str(self.downloaded)
        env["MUSICLOAD_SKIPPED"] = str(self.skipped)
        env["MUSICLOAD_DELETED"] = str(self.deleted)
        env["MUSICLOAD_FAILED"] = str(self.failed)
        env["MUSICLOAD_SUCCESS"] = "true" if self.success else "false"

        # Add any extra context
        if self.extra:
            for key, value in self.extra.items():
                env_key = f"MUSICLOAD_{key.upper()}"
                env[env_key] = str(value)

        return env


class HookRunner:
    """Runs hooks for events."""

    def __init__(self, hooks: list[HookConfig] | None = None):
        """Initialize hook runner.

        Args:
            hooks: List of hook configurations
        """
        self.hooks = hooks or []

    def run_hooks(self, context: HookContext) -> list[tuple[HookConfig, bool, str]]:
        """Run all hooks matching the given event.

        Args:
            context: Hook context with event data

        Returns:
            List of tuples: (hook_config, success, output/error)
        """
        results = []

        matching_hooks = [h for h in self.hooks if h.event == context.event]

        if not matching_hooks:
            logger.debug("No hooks configured for event: %s", context.event)
            return results

        logger.info(
            "Running %d hook(s) for event: %s",
            len(matching_hooks),
            context.event,
        )

        for hook in matching_hooks:
            # Skip hooks that shouldn't run on error
            if not context.success and not hook.run_on_error:
                logger.debug(
                    "Skipping hook (run_on_error=False and sync failed): %s",
                    hook.command[:50],
                )
                results.append((hook, False, "Skipped due to sync failure"))
                continue

            success, output = self._run_hook(hook, context)
            results.append((hook, success, output))

        return results

    def _run_hook(self, hook: HookConfig, context: HookContext) -> tuple[bool, str]:
        """Run a single hook command.

        Args:
            hook: Hook configuration
            context: Hook context with event data

        Returns:
            Tuple of (success, output/error message)
        """
        logger.info("Running hook command: %s", hook.command[:100])

        try:
            env = context.to_env()

            # Run the command
            result = subprocess.run(
                hook.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=hook.timeout,
                env=env,
            )

            if result.returncode == 0:
                logger.info("Hook completed successfully")
                if result.stdout:
                    logger.debug("Hook stdout: %s", result.stdout.strip())
                return True, result.stdout

            else:
                error_msg = result.stderr or f"Exit code: {result.returncode}"
                logger.error("Hook failed: %s", error_msg.strip())
                return False, error_msg

        except subprocess.TimeoutExpired:
            error_msg = f"Hook timed out after {hook.timeout} seconds"
            logger.error(error_msg)
            return False, error_msg

        except Exception as e:
            error_msg = f"Hook execution error: {e}"
            logger.error(error_msg)
            return False, error_msg


def parse_hooks_config(hooks_data: list[dict] | None) -> list[HookConfig]:
    """Parse hooks configuration from YAML data.

    Args:
        hooks_data: List of hook dictionaries from YAML

    Returns:
        List of HookConfig objects

    Raises:
        ValueError: If hook configuration is invalid
    """
    if not hooks_data:
        return []

    hooks = []
    valid_events = {"playlist_updated", "sync_completed"}

    for i, hook_dict in enumerate(hooks_data):
        if not isinstance(hook_dict, dict):
            raise ValueError(f"Hook {i+1} must be a dictionary")

        # Validate required fields
        if "event" not in hook_dict:
            raise ValueError(f"Hook {i+1} missing required field: event")
        if "command" not in hook_dict:
            raise ValueError(f"Hook {i+1} missing required field: command")

        event = hook_dict["event"]
        command = hook_dict["command"]

        # Validate event type
        if event not in valid_events:
            raise ValueError(
                f"Hook {i+1} has invalid event '{event}'. "
                f"Valid events: {', '.join(sorted(valid_events))}"
            )

        # Validate command
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"Hook {i+1} command must be a non-empty string")

        # Parse optional fields
        timeout = hook_dict.get("timeout", 60)
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValueError(f"Hook {i+1} timeout must be a positive integer")

        run_on_error = hook_dict.get("run_on_error", False)
        if not isinstance(run_on_error, bool):
            raise ValueError(f"Hook {i+1} run_on_error must be a boolean")

        hooks.append(
            HookConfig(
                event=event,
                command=command.strip(),
                timeout=timeout,
                run_on_error=run_on_error,
            )
        )

    logger.info("Loaded %d hook configuration(s)", len(hooks))
    return hooks
