"""musicload - Search and download music from YouTube Music with lyrics."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("musicload")
except PackageNotFoundError:
    # Allows local source checks before the package is installed.
    __version__ = "0.23.0"
