"""Unavailable video record model."""

from pydantic import BaseModel


class UnavailableRecord(BaseModel):
    """A single unavailable video record for JSON persistence."""

    failed_at: str
    error: str
    title: str | None = None
    artist: str | None = None
