"""Musicload plugin system."""

from musicload.plugins.registry import discover_plugins

# Auto-discover plugins on import
discover_plugins()
