"""Deezer track model."""

from pydantic import BaseModel, ConfigDict, computed_field


class DeezerTrack(BaseModel):
    """Represents a track from Deezer."""

    model_config = ConfigDict(frozen=True)

    name: str
    artist: str
    artists: list[str]
    album: str | None
    duration_ms: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def search_query(self) -> str:
        """Generate a search query for YouTube Music."""
        return f"{self.name} {self.artist}"
