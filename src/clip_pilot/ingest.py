"""Ingestion logic: hashing video files and registering sources.

This module is independent of how files are discovered (polling watcher,
URL downloads, manual drops), so it can be reused by the URL mode (T20).
"""

import hashlib
import logging
import sqlite3
from pathlib import Path

from clip_pilot import repo
from clip_pilot.constants import EVENT_SOURCE_CREATED, EVENT_SOURCE_DUPLICATE_SKIPPED

logger = logging.getLogger("clip_pilot.ingest")

DEFAULT_HASH_CHUNK_BYTES = 4 * 1024 * 1024


def compute_file_hash(path: Path, chunk_bytes: int = DEFAULT_HASH_CHUNK_BYTES) -> str | None:
    """Compute the SHA-256 of the first chunk_bytes of a file.

    Args:
        path: Path to the file.
        chunk_bytes: How many leading bytes to hash for deduplication.

    Returns:
        Hex digest, or None if the file cannot be read.
    """
    try:
        with open(path, "rb") as f:
            digest = hashlib.sha256()
            digest.update(f.read(chunk_bytes))
        return digest.hexdigest()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        logger.warning("Cannot hash file %s: %s", path, exc)
        return None


def ingest_file(conn: sqlite3.Connection, path: Path, *,
                hash_chunk_bytes: int = DEFAULT_HASH_CHUNK_BYTES,
                review_mode: str = "manual") -> int | None:
    """Register a video file as a source, skipping duplicates.

    Args:
        conn: Open database connection.
        path: Path to the video file.
        hash_chunk_bytes: Bytes to hash for deduplication.
        review_mode: Default review mode for the new source.

    Returns:
        New source id, or None if the file is a duplicate or ingestion failed.
    """
    path = Path(path)
    file_hash = compute_file_hash(path, hash_chunk_bytes)
    if file_hash is None:
        return None

    existing = repo.find_source_by_hash(conn, file_hash)
    if existing is not None:
        repo.add_event(
            conn,
            entity_type="source",
            entity_id=existing["id"],
            event_type=EVENT_SOURCE_DUPLICATE_SKIPPED,
            payload={"file_path": str(path)},
        )
        conn.commit()
        logger.info("Duplicate source skipped: source_id=%s hash=%s file=%s",
                    existing["id"], file_hash, path)
        return None

    try:
        source_id = repo.create_source(
            conn,
            file_path=str(path),
            file_hash=file_hash,
            title=path.stem,
            review_mode=review_mode,
        )
        repo.add_event(
            conn,
            entity_type="source",
            entity_id=source_id,
            event_type=EVENT_SOURCE_CREATED,
            payload={"file_path": str(path), "file_hash": file_hash},
        )
        conn.commit()
        logger.info("Source registered: id=%s file=%s", source_id, path)
        return source_id
    except sqlite3.Error as exc:
        conn.rollback()
        logger.error("Failed to register source for %s: %s", path, exc)
        return None
