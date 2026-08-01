"""Scene segmentation of analyzed sources into clip candidates.

Converts a probed source (status "done") into a set of clip candidates
(status "cut") by detecting scene boundaries with PySceneDetect.
"""

import logging
import sqlite3
from pathlib import Path

from clip_pilot import repo, scenes
from clip_pilot.config import Config
from clip_pilot.constants import (
    CLIP_STATUS_CUT,
    EVENT_CLIP_CREATED,
    EVENT_SOURCE_RESET,
    EVENT_SOURCE_SEGMENT_FAILED,
    EVENT_SOURCE_SEGMENTED,
    SOURCE_STATUS_DONE,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_SEGMENTED,
)

logger = logging.getLogger("clip_pilot.segmenter")


def segment_source(
    conn: sqlite3.Connection,
    source_id: int,
    config: Config,
    *,
    min_seconds: int | None = None,
    max_seconds: int | None = None,
) -> str | None:
    """Detect scenes in a source and create one clip candidate per scene.

    Args:
        conn: Open database connection.
        source_id: Source to segment.
        config: Application configuration.
        min_seconds: Override for the minimum clip length (config otherwise).
        max_seconds: Override for the maximum clip length (config otherwise).

    Returns:
        New source status, or None if the source is not in status "done".
    """
    source = repo.get_source(conn, source_id)
    if source is None:
        logger.warning("Source %s not found", source_id)
        return None
    if source["status"] != SOURCE_STATUS_DONE:
        return None

    existing = repo.count_clips_by_source(conn, source_id)
    if existing > 0:
        logger.info("Source %s already has %d clip(s), skipping", source_id, existing)
        return SOURCE_STATUS_SEGMENTED

    scene_config = config.video.get("scene", {})
    detector = scene_config.get("detector", scenes.DETECTOR_CONTENT)
    threshold = scene_config.get("threshold", 27.0)
    min_scene_seconds = scene_config.get("min_scene_seconds", 3.0)
    min_clip_seconds = (
        min_seconds if min_seconds is not None else config.video.get("min_clip_seconds", 15.0)
    )
    max_clip_seconds = (
        max_seconds if max_seconds is not None else config.video.get("max_clip_seconds", 60.0)
    )

    scene_bounds = scenes.detect_scenes(source["file_path"], detector=detector, threshold=threshold)
    if not scene_bounds:
        repo.update_source_status(conn, source_id, SOURCE_STATUS_FAILED, error="no scenes detected")
        repo.add_event(
            conn,
            entity_type="source",
            entity_id=source_id,
            event_type=EVENT_SOURCE_SEGMENT_FAILED,
            payload={"file_path": source["file_path"]},
        )
        conn.commit()
        logger.error("Source %s segmentation failed: no scenes detected", source_id)
        return SOURCE_STATUS_FAILED

    segments = scenes.merge_and_filter_scenes(
        scene_bounds,
        min_scene_seconds=min_scene_seconds,
        min_clip_seconds=min_clip_seconds,
        max_clip_seconds=max_clip_seconds,
    )

    for start, end in segments:
        clip_id = repo.create_clip(
            conn,
            source_id=source_id,
            start_time=start,
            end_time=end,
            status=CLIP_STATUS_CUT,
        )
        repo.add_event(
            conn,
            entity_type="clip",
            entity_id=clip_id,
            event_type=EVENT_CLIP_CREATED,
            payload={"start_time": start, "end_time": end, "source_id": source_id},
        )

    repo.update_source_status(conn, source_id, SOURCE_STATUS_SEGMENTED)
    repo.add_event(
        conn,
        entity_type="source",
        entity_id=source_id,
        event_type=EVENT_SOURCE_SEGMENTED,
        payload={"file_path": source["file_path"], "clip_count": len(segments)},
    )
    conn.commit()
    logger.info("Source %s segmented into %d clip(s)", source_id, len(segments))
    return SOURCE_STATUS_SEGMENTED


def segment_done_sources(
    conn: sqlite3.Connection,
    config: Config,
    *,
    min_seconds: int | None = None,
    max_seconds: int | None = None,
) -> int:
    """Segment all sources currently in status "done".

    Args:
        conn: Open database connection.
        config: Application configuration.
        min_seconds: Override for the minimum clip length (config otherwise).
        max_seconds: Override for the maximum clip length (config otherwise).

    Returns:
        Number of sources processed.
    """
    processed = 0
    for source in repo.get_sources_by_status(conn, SOURCE_STATUS_DONE):
        if (
            segment_source(
                conn,
                source["id"],
                config,
                min_seconds=min_seconds,
                max_seconds=max_seconds,
            )
            is not None
        ):
            processed += 1
    return processed


def reset_source(conn: sqlite3.Connection, source_id: int) -> int:
    """Delete all clips of a source and return it to status "done".

    Only allowed when the source is "segmented" and every clip is still in
    status "cut" (not yet formatted), so no work is lost.

    Args:
        conn: Open database connection.
        source_id: Source to reset.

    Returns:
        Number of deleted clips.

    Raises:
        ValueError: If the source is not segmented, or some clips are not "cut".
    """
    source = repo.get_source(conn, source_id)
    if source is None:
        raise ValueError(f"Source {source_id} not found")
    if source["status"] != SOURCE_STATUS_SEGMENTED:
        raise ValueError(f"Source {source_id} is not segmented (status={source['status']})")

    clips = repo.get_clips_by_source(conn, source_id)
    non_cut = [c for c in clips if c["status"] != CLIP_STATUS_CUT]
    if non_cut:
        raise ValueError(
            f"Cannot reset: {len(non_cut)} clip(s) are already formatted "
            f"(ready/formatting). Use 'review reject' to discard them first."
        )

    for clip in clips:
        clip_path = clip.get("path")
        if clip_path:
            Path(clip_path).unlink(missing_ok=True)

    removed = repo.delete_clips_by_source(conn, source_id)
    repo.update_source_status(conn, source_id, SOURCE_STATUS_DONE)
    repo.add_event(
        conn,
        entity_type="source",
        entity_id=source_id,
        event_type=EVENT_SOURCE_RESET,
        payload={"clips_deleted": len(removed)},
    )
    conn.commit()
    logger.info("Source %s reset to done, deleted %d clip(s)", source_id, len(removed))
    return len(removed)
