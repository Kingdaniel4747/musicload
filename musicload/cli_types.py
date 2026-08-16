"""Shared command-line option types."""

from enum import Enum


class AudioFormat(str, Enum):
    opus = "opus"
    mp3 = "mp3"
    flac = "flac"


class OrganizationMode(str, Enum):
    flat = "flat"
    album = "album"


class CookieMode(str, Enum):
    auto = "auto"
    always = "always"
    never = "never"
