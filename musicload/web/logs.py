"""Persistent process logs shared by the web and cron containers."""

import logging
from pathlib import Path


_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_process_file_logging(path: Path) -> None:
    """Append all Python logging output to a persistent process log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            if Path(handler.baseFilename).resolve() == resolved:
                return

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def read_log_chunk(path: Path, offset: int, chunk_size: int = 512 * 1024) -> dict:
    """Read a bounded raw-text chunk while preserving byte offsets."""
    if not path.exists():
        return {"content": "", "next_offset": 0, "eof": True}

    size = path.stat().st_size
    if offset > size:
        offset = 0
    with path.open("rb") as log_file:
        log_file.seek(offset)
        content = log_file.read(chunk_size)
        next_offset = log_file.tell()
    return {
        "content": content.decode("utf-8", errors="replace"),
        "next_offset": next_offset,
        "eof": next_offset >= size,
    }
