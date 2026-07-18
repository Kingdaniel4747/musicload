"""Job queue manager for async downloads with real-time progress tracking."""

import asyncio
import logging
import uuid
from asyncio import Queue, Task
from datetime import datetime
from typing import Optional

from musicload.config import get_config
from musicload.download import DownloadCancelledError, download
from musicload.models.queue import DownloadJob, JobStatus

logger = logging.getLogger(__name__)


class QueueManager:
    """Manages download job queue with async worker."""

    def __init__(self, max_history: int = 100):
        """Initialize queue manager.

        Args:
            max_history: Maximum number of completed/failed jobs to keep in history
        """
        self.queue: Queue[DownloadJob] = Queue()
        self.jobs: dict[str, DownloadJob] = {}
        self.worker_task: Optional[Task] = None
        self._running = False
        self._jobs_lock = asyncio.Lock()  # Protect concurrent access to self.jobs
        self._cancelled_job_ids: set[str] = set()
        self.max_history = max_history

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
        async with self._jobs_lock:
            self.jobs[job_id] = job
        await self.queue.put(job)
        logger.info("Added job to queue: %s - %s (id=%s)", artist, title, job_id)
        return job_id

    async def _worker(self):
        """Background worker that processes jobs from the queue."""
        logger.info("Worker started")
        while self._running:
            try:
                # Get job from queue with timeout
                try:
                    job = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

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
        logger.info("Processing job: %s - %s (id=%s)", job.artist, job.title, job.id)
        async with self._jobs_lock:
            existing_job = self.jobs.get(job.id)
            if existing_job is None:
                logger.info("Job no longer exists before processing (id=%s)", job.id)
                return
            existing_job.status = JobStatus.DOWNLOADING
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
            logger.info("Job completed: %s - %s (id=%s)", job.artist, job.title, job.id)

        except DownloadCancelledError:
            async with self._jobs_lock:
                self.jobs.pop(job.id, None)
                self._cancelled_job_ids.discard(job.id)
            logger.info("Cancelled download: %s - %s (id=%s)", job.artist, job.title, job.id)

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
                logger.info("Cleared job: %s (id=%s)", job.status.value, job_id)
                return True
            elif job.status == JobStatus.QUEUED:
                # Remove job immediately and mark its id so worker skips stale queue entry.
                del self.jobs[job_id]
                self._cancelled_job_ids.add(job_id)
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
