"""Polling watcher for the inbox folder.

Owns filesystem concerns (detection, settling, moving to staging) and
delegates source registration to the ingest module.
"""

import logging
import sqlite3
import time
from pathlib import Path

from clip_pilot.config import Config
from clip_pilot.ingest import ingest_file

logger = logging.getLogger("clip_pilot.watcher")

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v")


def detect_video_files(inbox: Path, extensions: tuple[str, ...] = VIDEO_EXTENSIONS) -> list[Path]:
    """Return video files currently present in the inbox folder.

    Args:
        inbox: Directory to scan.
        extensions: Allowed file extensions (lowercase, with leading dot).

    Returns:
        List of video file paths, sorted by name.
    """
    if not inbox.is_dir():
        return []
    return sorted(
        p for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() in extensions
    )


def is_file_settled(path: Path, settle_seconds: float = 2.0,
                    check_interval: float = 0.5) -> bool:
    """Return True if the file size stays stable for settle_seconds.

    Args:
        path: File to check.
        settle_seconds: How long the size must remain unchanged.
        check_interval: Delay between consecutive size measurements.

    Returns:
        True if stable, False if the file is missing, locked or still growing.
    """
    checks_needed = max(1, int(settle_seconds / check_interval))
    last_size = -1
    stable_count = 0
    for _ in range(checks_needed + 1):
        try:
            size = path.stat().st_size
        except (FileNotFoundError, PermissionError):
            return False
        if size > 0 and size == last_size:
            stable_count += 1
        else:
            stable_count = 0
        last_size = size
        if stable_count >= checks_needed:
            return True
        time.sleep(check_interval)
    return False


def move_to_staging(path: Path, staging_dir: Path) -> Path | None:
    """Move a file into the staging directory.

    Args:
        path: File to move.
        staging_dir: Destination directory (created if missing).

    Returns:
        Destination path, or None on failure.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    destination = staging_dir / path.name
    try:
        path.replace(destination)
        return destination
    except OSError as exc:
        logger.error("Failed to move %s to staging: %s", path, exc)
        return None


def scan_inbox(conn: sqlite3.Connection, config: Config) -> int:
    """Ingest all video files currently present in the inbox folder.

    Args:
        conn: Open database connection.
        config: Application configuration.

    Returns:
        Number of newly ingested sources.
    """
    inbox = config.get_path("inbox")
    staging = config.get_path("staging")
    extensions = tuple(config.watcher.get("extensions", VIDEO_EXTENSIONS))
    settle_seconds = config.watcher.get("settle_seconds", 2.0)
    hash_chunk_mb = config.watcher.get("hash_chunk_mb", 4)
    hash_chunk_bytes = int(hash_chunk_mb * 1024 * 1024)
    review_mode = config.review.get("default_mode", "manual")

    ingested = 0
    for path in detect_video_files(inbox, extensions):
        if not is_file_settled(path, settle_seconds):
            logger.debug("File not settled, skipping: %s", path)
            continue
        source_id = ingest_file(conn, path, hash_chunk_bytes=hash_chunk_bytes,
                                review_mode=review_mode)
        if source_id is None:
            continue
        if move_to_staging(path, staging) is None:
            logger.warning("Source %s registered but file could not be moved", source_id)
        ingested += 1
    return ingested


def watch_inbox(conn: sqlite3.Connection, config: Config) -> None:
    """Poll the inbox folder continuously and ingest new video files.

    Args:
        conn: Open database connection.
        config: Application configuration.
    """
    interval = config.watcher.get("scan_interval_seconds", 3)
    while True:
        try:
            scan_inbox(conn, config)
        except Exception:
            logger.exception("Watch loop iteration failed")
        time.sleep(interval)
