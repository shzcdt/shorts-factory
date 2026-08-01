"""Formatting of cut clips into ready Shorts-ready videos.

Consumes clips in status "cut", runs the ffmpeg pipeline and marks them
"ready" with a stored file path.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any, cast

from clip_pilot import ffmpeg_tools, repo
from clip_pilot.config import Config
from clip_pilot.constants import (
    CLIP_STATUS_CUT,
    CLIP_STATUS_FAILED,
    CLIP_STATUS_FORMATTING,
    CLIP_STATUS_READY,
    EVENT_CLIP_FORMAT_FAILED,
    EVENT_CLIP_FORMATTED,
)

logger = logging.getLogger("clip_pilot.formatter")


def _formatting_config(config: Config) -> dict[str, Any]:
    return cast(dict[str, Any], config.video.get("formatting", {}))


def _target_size(config: Config) -> tuple[int, int]:
    fmt = _formatting_config(config)
    return fmt.get("width", 1080), fmt.get("height", 1920)


def format_clip(conn: sqlite3.Connection, clip_id: int, config: Config) -> str | None:
    """Format a single clip in status "cut" via the ffmpeg pipeline.

    Args:
        conn: Open database connection.
        clip_id: Clip to format.
        config: Application configuration.

    Returns:
        New clip status, or None if the clip is not in status "cut".
    """
    clip = repo.get_clip(conn, clip_id)
    if clip is None:
        logger.warning("Clip %s not found", clip_id)
        return None
    if clip["status"] != CLIP_STATUS_CUT:
        return None

    source = repo.get_source(conn, clip["source_id"])
    if source is None:
        logger.warning("Source for clip %s not found", clip_id)
        return None

    source_path = Path(source["file_path"])
    if not source_path.exists():
        repo.update_clip_status(conn, clip_id, CLIP_STATUS_FAILED)
        repo.add_event(
            conn,
            entity_type="clip",
            entity_id=clip_id,
            event_type=EVENT_CLIP_FORMAT_FAILED,
            payload={"source_id": source["id"], "reason": "source_file_missing"},
        )
        conn.commit()
        logger.error("Clip %s failed: source file missing %s", clip_id, source_path)
        return CLIP_STATUS_FAILED

    fmt = _formatting_config(config)
    strategy = fmt.get("strategy", ffmpeg_tools.STRATEGY_CENTER_CROP)
    ffmpeg_path = config.video.get("ffmpeg_path", "ffmpeg")
    width, height = _target_size(config)

    output_path = config.get_path("clips") / f"{clip_id}.mp4"

    repo.update_clip_status(conn, clip_id, CLIP_STATUS_FORMATTING)
    conn.commit()

    ok, error = ffmpeg_tools.cut_and_format(
        source_path,
        output_path,
        start=clip["start_time"],
        end=clip["end_time"],
        ffmpeg_path=ffmpeg_path,
        width=width,
        height=height,
        strategy=strategy,
    )

    if not ok:
        repo.update_clip_status(conn, clip_id, CLIP_STATUS_FAILED, rejected_reason=error)
        repo.add_event(
            conn,
            entity_type="clip",
            entity_id=clip_id,
            event_type=EVENT_CLIP_FORMAT_FAILED,
            payload={"source_id": source["id"], "error": error},
        )
        conn.commit()
        logger.error("Clip %s formatting failed: %s", clip_id, error)
        return CLIP_STATUS_FAILED

    repo.update_clip_path(conn, clip_id, str(output_path))
    repo.update_clip_status(conn, clip_id, CLIP_STATUS_READY)
    repo.add_event(
        conn,
        entity_type="clip",
        entity_id=clip_id,
        event_type=EVENT_CLIP_FORMATTED,
        payload={"path": str(output_path), "source_id": source["id"]},
    )
    conn.commit()
    logger.info("Clip %s ready: %s", clip_id, output_path)
    return CLIP_STATUS_READY


def format_cut_clips(
    conn: sqlite3.Connection,
    config: Config,
    *,
    limit: int | None = None,
    source_id: int | None = None,
) -> int:
    """Format cut clips, optionally limited or scoped to one source.

    Args:
        conn: Open database connection.
        config: Application configuration.
        limit: Maximum number of clips to format.
        source_id: If set, only format clips of this source.

    Returns:
        Number of clips processed.
    """
    clips = repo.get_clips_by_status(conn, CLIP_STATUS_CUT)
    if source_id is not None:
        clips = [c for c in clips if c["source_id"] == source_id]
    if limit is not None:
        clips = clips[:limit]

    processed = 0
    for clip in clips:
        if format_clip(conn, clip["id"], config) is not None:
            processed += 1
    return processed
