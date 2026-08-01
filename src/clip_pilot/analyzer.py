"""Analysis of registered sources using ffprobe metadata."""

import logging
import sqlite3

from clip_pilot import repo
from clip_pilot.config import Config
from clip_pilot.constants import (
    EVENT_SOURCE_ANALYZED,
    EVENT_SOURCE_ANALYZE_FAILED,
    EVENT_SOURCE_RETRIED,
    EVENT_SOURCE_SKIPPED_TOO_SHORT,
    SOURCE_STATUS_DONE,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_NEW,
    SOURCE_STATUS_SKIPPED,
)
from clip_pilot.probe import probe_file

logger = logging.getLogger("clip_pilot.analyzer")

RETRYABLE_STATUSES = (SOURCE_STATUS_SKIPPED, SOURCE_STATUS_FAILED)


def analyze_source(conn: sqlite3.Connection, source_id: int, config: Config) -> str | None:
    """Probe a source and store its metadata.

    Args:
        conn: Open database connection.
        source_id: Source to analyze.
        config: Application configuration.

    Returns:
        New source status, or None if the source is not in status "new".
    """
    source = repo.get_source(conn, source_id)
    if source is None:
        logger.warning("Source %s not found", source_id)
        return None
    if source["status"] != SOURCE_STATUS_NEW:
        return None

    ffprobe_path = config.video.get("ffprobe_path", "ffprobe")
    metadata = probe_file(source["file_path"], ffprobe_path)
    if metadata is None:
        repo.update_source_status(conn, source_id, SOURCE_STATUS_FAILED,
                                  error="ffprobe could not read the file")
        repo.add_event(conn, entity_type="source", entity_id=source_id,
                       event_type=EVENT_SOURCE_ANALYZE_FAILED,
                       payload={"file_path": source["file_path"]})
        conn.commit()
        logger.error("Source %s analysis failed", source_id)
        return SOURCE_STATUS_FAILED

    min_seconds = config.video.get("min_clip_seconds", 15)
    duration = metadata["duration_seconds"]
    if duration is not None and duration < min_seconds:
        repo.update_source_status(conn, source_id, SOURCE_STATUS_SKIPPED,
                                  error="shorter than min_clip_seconds")
        repo.add_event(conn, entity_type="source", entity_id=source_id,
                       event_type=EVENT_SOURCE_SKIPPED_TOO_SHORT,
                       payload={"file_path": source["file_path"],
                                "duration_seconds": duration})
        conn.commit()
        logger.info("Source %s skipped: too short (%.1fs < %.1fs)",
                    source_id, duration, min_seconds)
        return SOURCE_STATUS_SKIPPED

    repo.update_source_metadata(
        conn, source_id,
        duration_seconds=metadata["duration_seconds"],
        resolution=metadata["resolution"],
        fps=metadata["fps"],
        metadata_json=metadata["metadata_json"],
    )
    repo.update_source_status(conn, source_id, SOURCE_STATUS_DONE)
    repo.add_event(conn, entity_type="source", entity_id=source_id,
                   event_type=EVENT_SOURCE_ANALYZED,
                   payload={"file_path": source["file_path"],
                            "duration_seconds": metadata["duration_seconds"],
                            "resolution": metadata["resolution"]})
    conn.commit()
    logger.info("Source %s analyzed: %.1fs %s @ %.2f fps",
                source_id, metadata["duration_seconds"] or 0.0,
                metadata["resolution"], metadata["fps"] or 0.0)
    return SOURCE_STATUS_DONE


def analyze_new_sources(conn: sqlite3.Connection, config: Config) -> int:
    """Analyze all sources in status "new".

    Args:
        conn: Open database connection.
        config: Application configuration.

    Returns:
        Number of sources processed.
    """
    processed = 0
    for source in repo.get_sources_by_status(conn, SOURCE_STATUS_NEW):
        if analyze_source(conn, source["id"], config) is not None:
            processed += 1
    return processed


def retry_source(conn: sqlite3.Connection, source_id: int) -> bool:
    """Reset a skipped or failed source back to "new" for re-analysis.

    Args:
        conn: Open database connection.
        source_id: Source to reset.

    Returns:
        True if the source was reset, False otherwise.
    """
    source = repo.get_source(conn, source_id)
    if source is None:
        logger.warning("Source %s not found", source_id)
        return False
    if source["status"] not in RETRYABLE_STATUSES:
        logger.warning("Source %s has status '%s', not retryable",
                       source_id, source["status"])
        return False
    repo.update_source_status(conn, source_id, SOURCE_STATUS_NEW, error=None)
    repo.add_event(conn, entity_type="source", entity_id=source_id,
                   event_type=EVENT_SOURCE_RETRIED,
                   payload={"previous_status": source["status"]})
    conn.commit()
    logger.info("Source %s reset to new (was %s)", source_id, source["status"])
    return True
