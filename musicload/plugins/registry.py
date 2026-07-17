"""Plugin discovery and registration."""

import logging
from importlib.metadata import entry_points
from typing import Type

from musicload.plugins.base import Plugin, PluginError

logger = logging.getLogger(__name__)

_PLUGIN_REGISTRY: dict[str, Type[Plugin]] = {}
_PLUGINS_DISCOVERED = False


def register_plugin(plugin_class: Type[Plugin]) -> None:
    """Register a plugin class.

    Args:
        plugin_class: Plugin class to register

    Raises:
        ValueError: If plugin name conflicts with different class
    """
    name = plugin_class().name
    if name in _PLUGIN_REGISTRY:
        # Allow re-registration of the same class
        if _PLUGIN_REGISTRY[name] is plugin_class:
            logger.debug("Plugin '%s' already registered, skipping", name)
            return
        raise ValueError(f"Plugin '{name}' already registered with different class")

    _PLUGIN_REGISTRY[name] = plugin_class
    logger.debug("Registered plugin: %s", name)


def get_plugin(name: str) -> Type[Plugin]:
    """Get a plugin class by name.

    Args:
        name: Plugin type name (e.g., 'listenbrainz', 'rss')

    Returns:
        Plugin class

    Raises:
        PluginError: If plugin not found
    """
    if name not in _PLUGIN_REGISTRY:
        raise PluginError(f"Plugin '{name}' not found. Available: {list_plugins()}")
    return _PLUGIN_REGISTRY[name]


def list_plugins() -> list[str]:
    """List all registered plugin names."""
    return sorted(_PLUGIN_REGISTRY.keys())


def discover_plugins() -> None:
    """Discover and register all plugins.

    Discovers:
    1. Built-in plugins (musicload.plugins.*)
    2. Third-party plugins via entry points

    This function is idempotent and can be called multiple times safely.
    """
    global _PLUGINS_DISCOVERED

    if _PLUGINS_DISCOVERED:
        logger.debug("Plugins already discovered, skipping")
        return

    # Register built-in plugins
    try:
        from musicload.plugins.billboard import BillboardPlugin
        from musicload.plugins.listenbrainz import ListenbrainzPlugin
        from musicload.plugins.reddit import RedditPlugin
        from musicload.plugins.rss import RSSPlugin

        register_plugin(BillboardPlugin)
        register_plugin(ListenbrainzPlugin)
        register_plugin(RedditPlugin)
        register_plugin(RSSPlugin)
        logger.debug("Registered built-in plugins")
    except ImportError as e:
        logger.warning("Failed to import built-in plugins: %s", e)

    # Discover third-party plugins via entry points
    try:
        eps = entry_points(group="musicload.plugins")
        for ep in eps:
            try:
                plugin_class = ep.load()
                register_plugin(plugin_class)
                logger.info("Loaded third-party plugin: %s", ep.name)
            except Exception as e:
                logger.warning("Failed to load plugin '%s': %s", ep.name, e)
    except Exception as e:
        logger.debug("Entry points not available or failed: %s", e)

    _PLUGINS_DISCOVERED = True
