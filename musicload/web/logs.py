"""Bounded in-memory log feed for the authenticated web interface."""

import logging
from collections import deque
from datetime import datetime, timezone
from threading import Lock


class WebLogHandler(logging.Handler):
    """Keep recent application log records without growing indefinitely."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self._records: deque[dict] = deque(maxlen=capacity)
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            item = {
                "timestamp": datetime.fromtimestamp(
                    record.created, tz=timezone.utc
                ).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record)[:4000],
            }
            with self._lock:
                self._records.append(item)
        except Exception:
            self.handleError(record)

    def recent(self, limit: int, minimum_level: int) -> list[dict]:
        with self._lock:
            records = list(self._records)
        filtered = [
            item
            for item in records
            if getattr(logging, item["level"], 0) >= minimum_level
        ]
        return filtered[-limit:]


web_log_handler = WebLogHandler()
web_log_handler.setFormatter(logging.Formatter("%(message)s"))


def install_web_log_handler() -> None:
    root = logging.getLogger()
    if web_log_handler not in root.handlers:
        root.addHandler(web_log_handler)
