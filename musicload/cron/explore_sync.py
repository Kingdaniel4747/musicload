"""Explore (charts/moods/genres) synchronization logic for cron mode.

Fetches tracks from YouTube Music charts or mood/genre playlists and
synchronizes them using the same infrastructure as playlist sync:
state tracking, download with lyrics, M3U playlist updates,
cross-reference protection.
"""

import logging
from datetime import datetime
from pathlib import Path

from musicload.config import get_config
from musicload.cron.config import ExploreConfig
from musicload.cron.state import PlaylistState, TrackState, get_state_dir, load_state, save_state
from musicload.cron.sync import SyncResult, compare_tracks, download_new_tracks, remove_old_tracks, update_m3u_playlist
from musicload.search import get_album_tracks, get_charts, get_mood_playlists, get_new_releases, get_playlist_tracks

logger = logging.getLogger(__name__)


def sync_explore(
    explore_config: ExploreConfig,
    download_dir: Path,
    audio_format: str,
    filename_template: str,
    organization_mode: str = "flat",
    use_primary_artist: bool = False,
) -> SyncResult:
    """Synchronize an explore source (charts or mood/genre).

    Fetches the current track list from YouTube Music, compares it with
    the saved state, downloads new tracks, optionally removes old ones,
    and updates the M3U playlist.

    Args:
        explore_config: Explore entry configuration
        download_dir: Download directory
        audio_format: Audio format (opus, mp3, flac)
        filename_template: Filename template for downloads
        organization_mode: File organization mode ("flat" or "album")
        use_primary_artist: Extract primary artist for folder (before feat., &, etc.)

    Returns:
        SyncResult with counts of operations performed
    """
    logger.info(
        "Starting explore sync: %s (type=%s)",
        explore_config.name,
        explore_config.type,
    )

    config = get_config()
    state_dir = get_state_dir(config.data_dir)

    try:
        # Fetch current tracks based on explore type
        current_tracks = fetch_explore_tracks(explore_config)
        if not current_tracks:
            logger.warning("No tracks found for explore source: %s", explore_config.name)
            return SyncResult(downloaded=0, skipped=0, deleted=0, failed=0)

        # Apply limit if configured (0 = no limit)
        if explore_config.limit > 0 and len(current_tracks) > explore_config.limit:
            logger.info(
                "Applying limit: %d -> %d tracks for %s",
                len(current_tracks),
                explore_config.limit,
                explore_config.name,
            )
            current_tracks = current_tracks[: explore_config.limit]

        logger.info("Found %d track(s) from explore source", len(current_tracks))

        # Load existing state (reuses the same PlaylistState model)
        state = load_state(state_dir, explore_config.name)
        if not state:
            state = PlaylistState(
                playlist_name=explore_config.name,
                url=_build_explore_url(explore_config),
                last_check=datetime.now().isoformat(),
                tracks=[],
            )
            logger.info("Created fresh state for explore source: %s", explore_config.name)

        # Compare tracks
        new_tracks, removed_tracks = compare_tracks(current_tracks, state)

        logger.info(
            "Changes detected: %d new, %d removed",
            len(new_tracks),
            len(removed_tracks),
        )

        # Download new tracks
        download_result = download_new_tracks(
            new_tracks,
            download_dir,
            audio_format,
            filename_template,
            state,
            organization_mode,
            use_primary_artist,
        )

        # Remove old tracks if sync=true
        deleted_count = 0
        if explore_config.sync and removed_tracks:
            deleted_count = remove_old_tracks(removed_tracks, state, download_dir, config.data_dir)

        # Update M3U playlist
        update_m3u_playlist(explore_config.name, state, download_dir)

        # Update and save state
        state.url = _build_explore_url(explore_config)
        state.last_check = datetime.now().isoformat()
        save_state(state_dir, state)

        result = SyncResult(
            downloaded=download_result["downloaded"],
            skipped=download_result["skipped"],
            deleted=deleted_count,
            failed=download_result["failed"],
        )

        logger.info(
            "Explore sync completed for %s: %d downloaded, %d skipped, %d deleted, %d failed",
            explore_config.name,
            result.downloaded,
            result.skipped,
            result.deleted,
            result.failed,
        )

        return result

    except Exception as e:
        logger.error("Explore sync failed for %s: %s", explore_config.name, e)
        return SyncResult(downloaded=0, skipped=0, deleted=0, failed=1)


def fetch_explore_tracks(explore_config: ExploreConfig) -> list[tuple[str, str, str]]:
    """Fetch current tracks from an explore source.

    For charts: fetches chart tracks via get_charts().
    For mood: fetches tracks from a specific playlist (if playlist_id is set)
    or from all playlists in the category (if playlist_id is not set).

    Args:
        explore_config: Explore entry configuration

    Returns:
        List of tuples: (video_id, title, artist)
    """
    config = get_config()
    if explore_config.type == "charts":
        return _fetch_chart_tracks(explore_config.country, allow_ugc=config.allow_ugc)
    elif explore_config.type == "mood":
        return _fetch_mood_tracks(explore_config.params, explore_config.playlist_id, allow_ugc=config.allow_ugc)
    elif explore_config.type == "new_releases":
        return _fetch_new_release_tracks()
    else:
        logger.error("Unknown explore type: %s", explore_config.type)
        return []


def _fetch_chart_tracks(country: str, allow_ugc: bool = False) -> list[tuple[str, str, str]]:
    """Fetch tracks from YouTube Music charts.

    Args:
        country: ISO 3166-1 Alpha-2 country code
        allow_ugc: If True, include UGC and OFFICIAL_SOURCE_MUSIC tracks.

    Returns:
        List of tuples: (video_id, title, artist)
    """
    try:
        charts = get_charts(country, allow_ugc=allow_ugc)
        tracks = []
        for chart_track in charts.tracks:
            if chart_track.video_id:
                tracks.append((chart_track.video_id, chart_track.title, chart_track.artist))
        logger.info("Fetched %d chart tracks for country %s", len(tracks), country)
        return tracks
    except Exception as e:
        logger.error("Failed to fetch chart tracks for %s: %s", country, e)
        return []


def _fetch_mood_tracks(params: str, playlist_id: str = "", allow_ugc: bool = False) -> list[tuple[str, str, str]]:
    """Fetch tracks from a mood/genre category.

    If playlist_id is provided, fetches tracks only from that specific playlist.
    Otherwise, fetches tracks from all playlists in the category (legacy behavior).

    Args:
        params: Mood/genre category params string
        playlist_id: Optional specific playlist ID to fetch from
        allow_ugc: If True, include UGC and OFFICIAL_SOURCE_MUSIC tracks.

    Returns:
        List of tuples: (video_id, title, artist)
    """
    try:
        # If playlist_id is specified, fetch only from that playlist
        if playlist_id:
            logger.info("Fetching tracks from specific playlist: %s", playlist_id)
            try:
                playlist_tracks = get_playlist_tracks(playlist_id, allow_ugc=allow_ugc)
                tracks = [
                    (track.video_id, track.title, track.artist)
                    for track in playlist_tracks
                    if track.video_id
                ]
                logger.info("Fetched %d tracks from playlist %s", len(tracks), playlist_id)
                return tracks
            except Exception as e:
                logger.error("Failed to fetch tracks from playlist %s: %s", playlist_id, e)
                return []

        # Legacy behavior: fetch from all playlists in the category
        playlists = get_mood_playlists(params)
        if not playlists:
            logger.warning("No playlists found for mood params: %s", params)
            return []

        logger.info("Found %d playlists for mood category", len(playlists))

        seen_ids = set()
        tracks = []

        for playlist in playlists:
            try:
                playlist_tracks = get_playlist_tracks(playlist.playlist_id, allow_ugc=allow_ugc)
                for track in playlist_tracks:
                    if track.video_id and track.video_id not in seen_ids:
                        seen_ids.add(track.video_id)
                        tracks.append((track.video_id, track.title, track.artist))
            except Exception as e:
                logger.warning(
                    "Failed to fetch tracks from playlist '%s' (%s): %s",
                    playlist.title,
                    playlist.playlist_id,
                    e,
                )

        logger.info("Fetched %d unique tracks from %d mood playlists", len(tracks), len(playlists))
        return tracks

    except Exception as e:
        logger.error("Failed to fetch mood playlists for params %s: %s", params, e)
        return []


def _fetch_new_release_tracks() -> list[tuple]:
    """Fetch tracks from all new release albums.

    Calls get_new_releases() to get the album list, then fetches tracks
    from each album via get_album_tracks().

    Returns:
        List of tuples: (video_id, title, artist, album metadata). The album
        metadata keeps each new-release album in one numbered folder.
    """
    try:
        albums = get_new_releases()
        if not albums:
            logger.warning("No new release albums found")
            return []

        logger.info("Found %d new release albums", len(albums))

        seen_ids: set[str] = set()
        tracks: list[tuple] = []

        for album in albums:
            try:
                album_tracks = get_album_tracks(album.browse_id)
                for track_number, track in enumerate(album_tracks, start=1):
                    if track.video_id and track.video_id not in seen_ids:
                        seen_ids.add(track.video_id)
                        tracks.append(
                            (
                                track.video_id,
                                track.title,
                                track.artist,
                                {
                                    "album": album.title,
                                    "album_artist": album.artist,
                                    "album_year": album.year,
                                    "track_number": track_number,
                                },
                            )
                        )
            except Exception as e:
                logger.warning(
                    "Failed to fetch tracks from album '%s' (%s): %s",
                    album.title,
                    album.browse_id,
                    e,
                )

        logger.info("Fetched %d unique tracks from %d new release albums", len(tracks), len(albums))
        return tracks

    except Exception as e:
        logger.error("Failed to fetch new releases: %s", e)
        return []


def _build_explore_url(explore_config: ExploreConfig) -> str:
    """Build a descriptive URL string for the state file.

    This is not a real URL but a human-readable identifier stored in
    the state file to describe the explore source.

    Args:
        explore_config: Explore entry configuration

    Returns:
        Descriptive string identifying the explore source
    """
    if explore_config.type == "charts":
        return f"explore:charts:{explore_config.country}"
    elif explore_config.type == "mood":
        if explore_config.playlist_id:
            return f"explore:mood:{explore_config.params}:playlist:{explore_config.playlist_id}"
        return f"explore:mood:{explore_config.params}"
    elif explore_config.type == "new_releases":
        return "explore:new_releases"
    return f"explore:{explore_config.type}"
