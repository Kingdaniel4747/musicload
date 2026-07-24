"""Scheduler for YouTube playlists and ListenBrainz recommendations."""

import logging
import signal
import sys
import threading
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from musicload.config import get_config
from musicload.cron.config import CronConfig, load_config
from musicload.cron.sync import sync_playlist

logger = logging.getLogger(__name__)


class CronScheduler:
    """Run the two supported cron sources on their configured schedules."""

    def __init__(self, config_path: Path, download_dir: Path | None = None):
        self.config_path = config_path
        self.cron_config: CronConfig | None = None
        self.scheduler: BackgroundScheduler | None = None
        self._config_signature: tuple[int, int] | None = None
        self._reload_lock = threading.Lock()

        main_config = get_config()
        self.download_dir = download_dir or main_config.download_dir
        self.audio_format = main_config.audio_format
        self.filename_template = main_config.filename_template
        self.organization_mode = main_config.organization_mode
        self.use_primary_artist = main_config.use_primary_artist

    def load_configuration(self) -> None:
        logger.info("Loading configuration from: %s", self.config_path)
        if self.config_path.exists():
            self.cron_config = load_config(self.config_path)
            stat = self.config_path.stat()
            self._config_signature = (stat.st_mtime_ns, stat.st_size)
        else:
            self.cron_config = CronConfig()
            self._config_signature = None
            logger.info("Cron configuration does not exist yet; waiting for web configuration")

    def start(self) -> None:
        if not self.cron_config:
            self.load_configuration()

        self.scheduler = BackgroundScheduler()

        for playlist_config in self.cron_config.playlists.values():
            self._schedule_playlist(playlist_config)
        for plugin_config in self.cron_config.plugins.values():
            self._schedule_plugin(plugin_config)

        self.scheduler.add_job(
            self._reload_if_changed,
            "interval",
            seconds=5,
            id="_musicload_config_reload",
            name="Reload cron configuration",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Cron scheduler started successfully")

    def _reload_if_changed(self) -> None:
        """Reload jobs after an atomic config update from the web UI."""
        with self._reload_lock:
            if not self.config_path.exists():
                signature = None
            else:
                stat = self.config_path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            if signature == self._config_signature:
                return

            try:
                new_config = load_config(self.config_path) if signature else CronConfig()
            except (OSError, ValueError) as exc:
                logger.error("Ignoring invalid cron configuration update: %s", exc)
                return

            for job in self.scheduler.get_jobs():
                if job.id != "_musicload_config_reload":
                    job.remove()
            self.cron_config = new_config
            self._config_signature = signature
            for playlist_config in new_config.playlists.values():
                self._schedule_playlist(playlist_config)
            for plugin_config in new_config.plugins.values():
                self._schedule_plugin(plugin_config)
            logger.info("Reloaded cron configuration")

    def _schedule_playlist(self, playlist_config) -> None:
        self.scheduler.add_job(
            func=self._sync_job,
            trigger=CronTrigger.from_crontab(playlist_config.schedule),
            args=[playlist_config],
            id=playlist_config.name,
            name=f"YouTube playlist: {playlist_config.name}",
            replace_existing=True,
        )
        logger.info(
            "Scheduled YouTube playlist '%s' with cron: %s",
            playlist_config.name,
            playlist_config.schedule,
        )

    def _sync_job(self, playlist_config) -> None:
        try:
            result = sync_playlist(
                playlist_config=playlist_config,
                download_dir=self.download_dir,
                audio_format=self.audio_format,
                filename_template=self.filename_template,
                organization_mode=self.organization_mode,
                use_primary_artist=self.use_primary_artist,
            )
            self._notify(playlist_config.name, "playlist", result, True)
        except Exception as exc:
            logger.error("Playlist sync failed for %s: %s", playlist_config.name, exc)
            self._notify(playlist_config.name, "playlist", None, False, str(exc))

    def _schedule_plugin(self, plugin_config) -> None:
        self.scheduler.add_job(
            func=self._plugin_sync_job,
            trigger=CronTrigger.from_crontab(plugin_config.schedule),
            args=[plugin_config],
            id=f"listenbrainz_{plugin_config.name}",
            name=f"ListenBrainz: {plugin_config.name}",
            replace_existing=True,
        )
        logger.info(
            "Scheduled ListenBrainz job '%s' with cron: %s",
            plugin_config.name,
            plugin_config.schedule,
        )

    def _plugin_sync_job(self, plugin_config) -> None:
        try:
            from musicload.plugins.base import PluginConfig
            from musicload.plugins.listenbrainz import ListenbrainzPlugin
            from musicload.plugins.sync import sync_plugin_instance

            config = PluginConfig(
                name=plugin_config.name,
                download_dir=self.download_dir,
                audio_format=self.audio_format,
                filename_template=self.filename_template,
                config=plugin_config.config,
                organization_mode=self.organization_mode,
                use_primary_artist=self.use_primary_artist,
            )
            result = sync_plugin_instance(
                ListenbrainzPlugin(),
                config,
                sync_mode=plugin_config.sync,
            )
            self._notify(plugin_config.name, "listenbrainz", result, True)
        except Exception as exc:
            logger.error("ListenBrainz sync failed for %s: %s", plugin_config.name, exc)
            self._notify(plugin_config.name, "listenbrainz", None, False, str(exc))

    @staticmethod
    def _notify(name: str, sync_type: str, result, success: bool, error: str | None = None) -> None:
        from musicload.notifications import send_sync_notification

        send_sync_notification(
            name=name,
            sync_type=sync_type,
            result=result,
            success=success,
            error=error,
        )

    def sync_all_once(self) -> None:
        if not self.cron_config:
            self.load_configuration()

        logger.info("Running all configured cron jobs once")
        for playlist_config in self.cron_config.playlists.values():
            self._sync_job(playlist_config)
        for plugin_config in self.cron_config.plugins.values():
            self._plugin_sync_job(plugin_config)
        logger.info("All configured cron jobs finished")

    def stop(self) -> None:
        if self.scheduler:
            logger.info("Stopping cron scheduler")
            self.scheduler.shutdown(wait=True)

    def run_forever(self) -> None:
        def handle_shutdown(signum, frame):
            logger.info("Received shutdown signal %s", signum)
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)
        self.start()
        logger.info("Cron scheduler is running")

        try:
            while True:
                signal.pause()
        except (KeyboardInterrupt, SystemExit):
            self.stop()
