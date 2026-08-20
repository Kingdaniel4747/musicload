"""Job queue manager for async downloads with real-time progress tracking."""

import asyncio
import json
import logging
import uuid
from asyncio import Queue, Task
from datetime import datetime
from pathlib import Path
from typing import Optional

from musicload.config import get_config
from musicload.models.queue import DownloadJob, JobStatus
from musicload.playlist import add_to_m3u

logger = logging.getLogger(__name__)


class QueueManager:
    """Manages download job queue with async worker."""

    def __init__(
        self,
        max_history: int = 100,
        history_path: Path | None = None,
    ):
        """Initialize queue manager.

        Args:
            max_history: Maximum number of completed/failed jobs to keep in history
            history_path: Optional JSON file used to preserve download history
                across application/container restarts.
        """
        self.queue: Queue[DownloadJob] = Queue()
        self.jobs: dict[str, DownloadJob] = {}
        self.worker_task: Optional[Task] = None
        self._running = False
        self._jobs_lock = asyncio.Lock()  # Protect concurrent access to self.jobs
        self._cancelled_job_ids: set[str] = set()
        self.max_history = max_history
        self.history_path = history_path
        self._load_history()

    def _load_history(self) -> None:
        """Restore persisted jobs and mark interrupted transfers as failed."""
        if not self.history_path or not self.history_path.is_file():
            return

        changed = False
        try:
            raw_jobs = json.loads(self.history_path.read_text(encoding="utf-8"))
            if not isinstance(raw_jobs, list):
                raise ValueError("download history must contain a JSON list")
            restored: list[DownloadJob] = []
            restored_paths: set[str] = set()
            for raw_job in raw_jobs:
                try:
                    job = DownloadJob.model_validate(raw_job)
                except (TypeError, ValueError):
                    changed = True
                    continue
                if job.status in (JobStatus.QUEUED, JobStatus.DOWNLOADING):
                    job.status = JobStatus.FAILED
                    job.error = "Download interrupted by application restart"
                    job.completed_at = datetime.now()
                    changed = True
                if job.status == JobStatus.CANCELLED:
                    changed = True
                    continue
                if job.status == JobStatus.COMPLETED and job.file_path:
                    normalized_path = str(Path(job.file_path).resolve())
                    if not Path(job.file_path).is_file() or normalized_path in restored_paths:
                        changed = True
                        continue
                    restored_paths.add(normalized_path)
                restored.append(job)

            restored.sort(key=lambda item: item.created_at, reverse=True)
            for job in restored[: self.max_history]:
                self.jobs[job.id] = job
            changed = changed or len(restored) > self.max_history
            logger.info("Restored %d download history entries", len(self.jobs))
        except (OSError, json.JSONDecodeError, ValueError):
            logger.warning(
                "Could not restore download history from %s",
                self.history_path,
                exc_info=True,
            )
            return

        if changed:
            self._persist_history_locked()

    def _persist_history_locked(self) -> None:
        """Atomically persist the current jobs; caller must hold the jobs lock."""
        if not self.history_path:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.history_path.with_name(
                f".{self.history_path.name}.tmp"
            )
            jobs = sorted(
                self.jobs.values(), key=lambda item: item.created_at, reverse=True
            )
            payload = [
                job.model_dump(mode="json") for job in jobs[: self.max_history]
            ]
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary_path.replace(self.history_path)
        except OSError:
            logger.warning(
                "Could not persist download history to %s",
                self.history_path,
                exc_info=True,
            )

    async def start(self):
        """Start the background worker."""
        if self._running:
            return
        self._running = True
        self.worker_task = asyncio.create_task(self._worker())
        logger.info("Queue manager started")

    async def stop(self):
        """Stop the background worker gracefully."""
        self._running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        async with self._jobs_lock:
            self._cancelled_job_ids.clear()
        logger.info("Queue manager stopped")

    async def add_job(
        self,
        video_id: str,
        title: str,
        artist: str,
        format: str = "opus",
        artists: Optional[list[str]] = None,
        playlist_name: Optional[str] = None,
        album: Optional[str] = None,
        album_artist: Optional[str] = None,
        album_year: Optional[int] = None,
        track_number: Optional[int] = None,
    ) -> str:
        """
        Add a download job to the queue.

        Args:
            video_id: YouTube video ID
            title: Track title
            artist: Track artist
            format: Audio format (opus, mp3, flac)
            artists: List of individual artist names for multi-value tags
            playlist_name: Resolved playlist name for this job (from Remote-User header)

        Returns:
            Job ID
        """
        async with self._jobs_lock:
            duplicate = next(
                (
                    item
                    for item in self.jobs.values()
                    if item.video_id == video_id
                    and item.format == format
                    and item.status in (JobStatus.QUEUED, JobStatus.DOWNLOADING)
                ),
                None,
            )
            if duplicate:
                return duplicate.id
            job_id = str(uuid.uuid4())
            job = DownloadJob(
                id=job_id,
                video_id=video_id,
                title=title,
                artist=artist,
                format=format,
                status=JobStatus.QUEUED,
                artists=artists,
                playlist_name=playlist_name,
                album=album,
                album_artist=album_artist,
                album_year=album_year,
                track_number=track_number,
            )
            self.jobs[job_id] = job
            self._persist_history_locked()
        await self.queue.put(job)
        logger.info("Added job to queue: %s - %s (id=%s)", artist, title, job_id)
        return job_id

    async def find_active_job(self, video_id: str, format: str) -> Optional[DownloadJob]:
        """Return an already queued/downloading copy of the requested format."""
        async with self._jobs_lock:
            return next(
                (
                    job
                    for job in self.jobs.values()
                    if job.video_id == video_id
                    and job.format == format
                    and job.status in (JobStatus.QUEUED, JobStatus.DOWNLOADING)
                ),
                None,
            )

    async def restore_library_files(self, records: list[dict]) -> int:
        """Add existing on-disk audio files missing from download history."""
        restored = 0
        changed = False
        restored_ids: set[str] = set()
        async with self._jobs_lock:
            known_paths = {
                str(Path(job.file_path).resolve())
                for job in self.jobs.values()
                if job.file_path
            }
            for record in sorted(
                records, key=lambda item: item["modified_at"], reverse=True
            ):
                file_path = Path(record["file_path"])
                normalized_path = str(file_path.resolve())
                if normalized_path in known_paths or not file_path.is_file():
                    continue
                completed_at = datetime.fromtimestamp(record["modified_at"])
                job_id = f"library-{uuid.uuid5(uuid.NAMESPACE_URL, normalized_path)}"
                self.jobs[job_id] = DownloadJob(
                    id=job_id,
                    video_id="",
                    title=record["title"] or file_path.stem,
                    artist=record["artist"] or "",
                    format=file_path.suffix.lower().lstrip("."),
                    status=JobStatus.COMPLETED,
                    progress=100.0,
                    file_path=str(file_path),
                    created_at=completed_at,
                    completed_at=completed_at,
                    album=record.get("album"),
                )
                known_paths.add(normalized_path)
                restored += 1
                restored_ids.add(job_id)
                changed = True

            completed_failed = [
                job
                for job in self.jobs.values()
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED)
            ]
            if len(completed_failed) > self.max_history:
                completed_failed.sort(
                    key=lambda job: job.completed_at or job.created_at,
                    reverse=True,
                )
                keep_ids = {job.id for job in completed_failed[: self.max_history]}
                for job in completed_failed[self.max_history :]:
                    self.jobs.pop(job.id, None)
                    changed = True
                restored = sum(1 for job_id in restored_ids if job_id in keep_ids)

            if changed:
                self._persist_history_locked()
        if restored:
            logger.info("Added %d existing library files to download history", restored)
        return restored

    async def _worker(self):
        """Background worker that processes jobs from the queue."""
        logger.info("Worker started")
        while self._running:
            try:
                # Queue.get blocks without polling, keeping idle CPU usage near zero.
                job = await self.queue.get()

                # Skip jobs that were removed before the worker picked them up.
                async with self._jobs_lock:
                    should_skip = (
                        job.id in self._cancelled_job_ids
                        or job.id not in self.jobs
                        or self.jobs[job.id].status != JobStatus.QUEUED
                    )
                    if should_skip:
                        self._cancelled_job_ids.discard(job.id)

                if should_skip:
                    logger.info("Skipping removed/cancelled job (id=%s)", job.id)
                    continue

                # Process the job
                await self._process_job(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Worker error: %s", e)

        logger.info("Worker stopped")

    async def _process_job(self, job: DownloadJob):
        """
        Process a single download job.

        Args:
            job: The job to process
        """
        from musicload.download import (
            DownloadCancelledError,
            ExistingDownloadError,
            download,
        )

        logger.info("Processing job: %s - %s (id=%s)", job.artist, job.title, job.id)
        async with self._jobs_lock:
            existing_job = self.jobs.get(job.id)
            if existing_job is None:
                logger.info("Job no longer exists before processing (id=%s)", job.id)
                return
            existing_job.status = JobStatus.DOWNLOADING
            self._persist_history_locked()
        config = get_config()

        def progress_callback(progress_data: dict):
            """Update job progress from yt-dlp hook."""
            job.progress = progress_data.get("percent", 0.0)
            job.speed = progress_data.get("speed", "")
            job.eta = progress_data.get("eta", "")

        try:
            # Run download in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            audio_path = await loop.run_in_executor(
                None,
                lambda: download(
                    video_id=job.video_id,
                    output_dir=config.download_dir,
                    audio_format=job.format,
                    filename_template=config.filename_template,
                    fetch_lyrics=True,
                    progress_callback=progress_callback,
                    organization_mode=config.organization_mode,
                    use_primary_artist=config.use_primary_artist,
                    cookie_file=config.cookie_file_path,
                    artists=job.artists,
                    apply_replaygain=config.replaygain,
                    album=job.album,
                    album_artist=job.album_artist,
                    album_year=job.album_year,
                    track_number=job.track_number,
                    should_cancel=lambda: job.id in self._cancelled_job_ids,
                    report_existing=True,
                ),
            )

            async with self._jobs_lock:
                current_job = self.jobs.get(job.id)
                if current_job is None:
                    logger.info("Job removed while finishing (id=%s)", job.id)
                    return
                current_job.status = JobStatus.COMPLETED
                current_job.file_path = str(audio_path) if audio_path else None
                current_job.completed_at = datetime.now()
                current_job.progress = 100.0
                if audio_path:
                    completed_path = str(Path(audio_path).resolve())
                    duplicate_ids = [
                        existing.id
                        for existing in self.jobs.values()
                        if existing.id != current_job.id
                        and existing.file_path
                        and str(Path(existing.file_path).resolve()) == completed_path
                    ]
                    for duplicate_id in duplicate_ids:
                        self.jobs.pop(duplicate_id, None)
                self._persist_history_locked()
            if job.playlist_name and audio_path:
                add_to_m3u([audio_path], job.playlist_name, config.download_dir)
            logger.info("Job completed: %s - %s (id=%s)", job.artist, job.title, job.id)
            from musicload.notifications import send_download_notification

            await asyncio.to_thread(
                send_download_notification,
                job.title,
                job.artist,
                success=True,
            )

        except (DownloadCancelledError, ExistingDownloadError) as error:
            if isinstance(error, ExistingDownloadError) and job.playlist_name:
                add_to_m3u([error.path], job.playlist_name, config.download_dir)
            async with self._jobs_lock:
                self.jobs.pop(job.id, None)
                self._cancelled_job_ids.discard(job.id)
                self._persist_history_locked()
            logger.info("Download omitted: %s - %s (id=%s): %s", job.artist, job.title, job.id, error)

        except Exception as e:
            logger.exception("Job failed: %s - %s (id=%s): %s", job.artist, job.title, job.id, e)
            async with self._jobs_lock:
                current_job = self.jobs.get(job.id)
                if current_job is None:
                    logger.info("Job removed while failing (id=%s)", job.id)
                    return
                current_job.status = JobStatus.FAILED
                current_job.error = str(e)
                current_job.completed_at = datetime.now()
                self._persist_history_locked()
            from musicload.notifications import send_download_notification

            await asyncio.to_thread(
                send_download_notification,
                job.title,
                job.artist,
                success=False,
                error=str(e),
            )

        # Cleanup old jobs to prevent memory leak
        await self.cleanup_old_jobs()

    async def get_job(self, job_id: str) -> Optional[DownloadJob]:
        """
        Get a job by ID.

        Args:
            job_id: The job ID

        Returns:
            The job or None if not found
        """
        async with self._jobs_lock:
            return self.jobs.get(job_id)

    async def remove_jobs_for_file(self, file_path: Path) -> int:
        """Remove completed history entries that refer to one deleted file."""
        normalized_path = str(file_path.resolve())
        async with self._jobs_lock:
            matching_ids = [
                job.id
                for job in self.jobs.values()
                if job.file_path
                and str(Path(job.file_path).resolve()) == normalized_path
            ]
            for job_id in matching_ids:
                self.jobs.pop(job_id, None)
            if matching_ids:
                self._persist_history_locked()
        return len(matching_ids)

    async def list_jobs(self) -> list[DownloadJob]:
        """
        List all jobs ordered by creation time (newest first).

        Returns:
            List of jobs
        """
        async with self._jobs_lock:
            return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    async def remove_job(self, job_id: str) -> bool:
        """
        Remove a job from the queue or clear if completed/failed.

        Args:
            job_id: The job ID to remove

        Returns:
            True if removed, False if not found
        """
        async with self._jobs_lock:
            job = self.jobs.get(job_id)
            if not job:
                return False

            # Completed jobs are simply cleared from history.
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                del self.jobs[job_id]
                self._cancelled_job_ids.discard(job_id)
                self._persist_history_locked()
                logger.info("Cleared job: %s (id=%s)", job.status.value, job_id)
                return True
            elif job.status == JobStatus.QUEUED:
                # Remove job immediately and mark its id so worker skips stale queue entry.
                del self.jobs[job_id]
                self._cancelled_job_ids.add(job_id)
                self._persist_history_locked()
                logger.info("Cancelled and removed queued job (id=%s)", job_id)
                return True

            elif job.status == JobStatus.DOWNLOADING:
                # The yt-dlp progress hook observes this flag and stops the
                # active transfer at the next progress update.
                self._cancelled_job_ids.add(job_id)
                logger.info("Cancellation requested for active job (id=%s)", job_id)
                return True

            return False

    async def cancel_all(self) -> int:
        """Cancel all queued and currently downloading jobs."""
        async with self._jobs_lock:
            job_ids = [
                job.id
                for job in self.jobs.values()
                if job.status in (JobStatus.QUEUED, JobStatus.DOWNLOADING)
            ]
        cancelled = 0
        for job_id in job_ids:
            if await self.remove_job(job_id):
                cancelled += 1
        return cancelled

    async def cleanup_old_jobs(self):
        """Remove old completed/failed jobs beyond max_history limit.

        Keeps the most recent completed/failed jobs up to max_history.
        Active (queued/downloading) jobs are never removed.
        """
        async with self._jobs_lock:
            # Separate completed/failed jobs from active jobs
            completed_failed = [
                job for job in self.jobs.values()
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED)
            ]

            # If we're over the limit, remove oldest jobs
            if len(completed_failed) > self.max_history:
                # Sort by completion time (oldest first)
                completed_failed.sort(
                    key=lambda j: j.completed_at or j.created_at
                )

                # Remove oldest jobs beyond the limit
                num_to_remove = len(completed_failed) - self.max_history
                for job in completed_failed[:num_to_remove]:
                    del self.jobs[job.id]
                    logger.debug(
                        "Cleaned up old job: %s (id=%s, completed=%s)",
                        job.status.value,
                        job.id,
                        job.completed_at
                    )

                self._persist_history_locked()

                logger.info(
                    "Cleaned up %d old jobs (keeping %d most recent)",
                    num_to_remove,
                    self.max_history
                )

    async def get_stats(self) -> dict:
        """
        Get queue statistics.

        Returns:
            Dict with queue stats
        """
        async with self._jobs_lock:
            queued = sum(1 for j in self.jobs.values() if j.status == JobStatus.QUEUED)
            downloading = sum(1 for j in self.jobs.values() if j.status == JobStatus.DOWNLOADING)
            completed = sum(1 for j in self.jobs.values() if j.status == JobStatus.COMPLETED)
            failed = sum(1 for j in self.jobs.values() if j.status == JobStatus.FAILED)

            return {
                "total": len(self.jobs),
                "queued": queued,
                "downloading": downloading,
                "completed": completed,
                "failed": failed,
            }
