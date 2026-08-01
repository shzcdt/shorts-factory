"""Scene segmentation of analyzed sources into clip candidates.

Converts a probed source (status "done") into a set of clip candidates
(status "cut") by detecting scene boundaries with PySceneDetect.
"""

import logging
import sqlite3

from clip_pilot import repo, scenes
from clip_pilot.config import Config
from clip_pilot.constants import (
    CLIP_STATUS_CUT,
    EVENT_CLIP_CREATED,
    EVENT_SOURCE_SEGMENT_FAILED,
    EVENT_SOURCE_SEGMENTED,
    SOURCE_STATUS_DONE,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_SEGMENTED,
)

logger = logging.getLogger("clip_pilot.segmenter")


def segment_source(conn: sqlite3.Connection, source_id: int, config: Config) -> str | None:
    """Detect scenes in a source and create one clip candidate per scene.

    Args:
        conn: Open database connection.
        source_id: Source to segment.
        config: Application configuration.

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
    max_clip_seconds = config.video.get("max_clip_seconds", 60.0)

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


def segment_done_sources(conn: sqlite3.Connection, config: Config) -> int:
    """Segment all sources currently in status "done".

    Args:
        conn: Open database connection.
        config: Application configuration.

    Returns:
        Number of sources processed.
    """
    processed = 0
    for source in repo.get_sources_by_status(conn, SOURCE_STATUS_DONE):
        if segment_source(conn, source["id"], config) is not None:
            processed += 1
    return processed
