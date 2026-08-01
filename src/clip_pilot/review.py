"""Review workflow: move ready clips into the review folder and decide their fate.

Consumes clips in status "ready", moves them into the review folder (status
"review") for human inspection, then approves or rejects each one. Approved
clips stay put until publishing (T16); rejected clips move to a separate
folder so humans never confuse them with pending ones.
"""

import logging
import os
import re
import sqlite3
from pathlib import Path

from clip_pilot import repo
from clip_pilot.config import Config
from clip_pilot.constants import (
    CLIP_STATUS_APPROVED,
    CLIP_STATUS_FAILED,
    CLIP_STATUS_READY,
    CLIP_STATUS_REJECTED,
    CLIP_STATUS_REVIEW,
    EVENT_CLIP_APPROVED,
    EVENT_CLIP_REJECTED,
    EVENT_CLIP_REVIEW_PREPARED,
)

logger = logging.getLogger("clip_pilot.review")

_FILENAME_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FILENAME_MAX_STEM = 30


def _sanitize_stem(stem: str) -> str:
    """Strip characters invalid in Windows filenames and trim the length."""
    cleaned = _FILENAME_ILLEGAL.sub("_", stem).strip(" .")
    if not cleaned:
        return "clip"
    return cleaned[:_FILENAME_MAX_STEM]


def _review_filename(source_path: str, start_time: float, clip_id: int) -> str:
    """Build a human-readable review filename.

    The clip id is appended to guarantee uniqueness even if two clips share
    a source and a start time.
    """
    stem = _sanitize_stem(Path(source_path).stem)
    return f"{stem}_{int(start_time)}s_{clip_id}.mp4"


def _move_clip_file(conn: sqlite3.Connection, clip_id: int, src: Path, dst: Path) -> bool:
    """Move a clip file and update the stored path, or False on OSError."""
    try:
        os.replace(src, dst)
    except OSError as exc:
        logger.error("Cannot move clip %s: %s -> %s: %s", clip_id, src, dst, exc)
        return False
    repo.update_clip_path(conn, clip_id, str(dst))
    return True


def prepare_for_review(
    conn: sqlite3.Connection,
    config: Config,
    *,
    limit: int | None = None,
    source_id: int | None = None,
) -> int:
    """Move ready clips into the review folder, marking them "review".

    Idempotent: only clips in status "ready" are considered, so a second run
    is a no-op. Clips whose file is missing are marked "failed" with an event.

    Args:
        conn: Open database connection.
        config: Application configuration.
        limit: Maximum number of clips to move.
        source_id: If set, only move clips of this source.

    Returns:
        Number of clips moved to review.
    """
    clips = repo.get_clips_by_status(conn, CLIP_STATUS_READY)
    if source_id is not None:
        clips = [c for c in clips if c["source_id"] == source_id]
    if limit is not None:
        clips = clips[:limit]

    review_dir = config.get_path("review")
    review_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for clip in clips:
        clip_id = clip["id"]
        source = repo.get_source(conn, clip["source_id"])
        if source is None:
            logger.warning("Source for clip %s not found", clip_id)
            continue

        clip_path = clip["path"]
        if clip_path is None or not Path(clip_path).exists():
            repo.update_clip_status(conn, clip_id, CLIP_STATUS_FAILED)
            repo.add_event(
                conn,
                entity_type="clip",
                entity_id=clip_id,
                event_type=EVENT_CLIP_REVIEW_PREPARED,
                payload={"reason": "source_file_missing", "path": str(clip_path)},
            )
            conn.commit()
            logger.error("Clip %s failed: file missing %s", clip_id, clip_path)
            continue

        src = Path(clip_path)

        dst = review_dir / _review_filename(source["file_path"], clip["start_time"], clip_id)
        if not _move_clip_file(conn, clip_id, src, dst):
            repo.update_clip_status(conn, clip_id, CLIP_STATUS_FAILED)
            repo.add_event(
                conn,
                entity_type="clip",
                entity_id=clip_id,
                event_type=EVENT_CLIP_REVIEW_PREPARED,
                payload={"reason": "move_failed", "path": str(src)},
            )
            conn.commit()
            continue

        repo.update_clip_status(conn, clip_id, CLIP_STATUS_REVIEW)
        repo.add_event(
            conn,
            entity_type="clip",
            entity_id=clip_id,
            event_type=EVENT_CLIP_REVIEW_PREPARED,
            payload={"path": str(dst), "source_id": source["id"]},
        )
        conn.commit()
        logger.info("Clip %s ready for review: %s", clip_id, dst)
        moved += 1
    return moved


def list_clips_for_review(conn: sqlite3.Connection) -> list[dict]:
    """Return all clips currently pending review."""
    return repo.get_clips_by_status(conn, CLIP_STATUS_REVIEW)


def approve_clip(conn: sqlite3.Connection, clip_id: int) -> str | None:
    """Approve a clip pending review.

    Args:
        conn: Open database connection.
        clip_id: Clip to approve.

    Returns:
        New clip status, or None if the clip is not pending review.

    Raises:
        ValueError: If the clip id does not exist.
    """
    clip = repo.get_clip(conn, clip_id)
    if clip is None:
        raise ValueError(f"Clip {clip_id} not found")
    if clip["status"] != CLIP_STATUS_REVIEW:
        return None

    repo.update_clip_status(conn, clip_id, CLIP_STATUS_APPROVED, review_decision="approved")
    repo.add_event(
        conn,
        entity_type="clip",
        entity_id=clip_id,
        event_type=EVENT_CLIP_APPROVED,
        payload={"path": clip["path"]},
    )
    conn.commit()
    logger.info("Clip %s approved", clip_id)
    return CLIP_STATUS_APPROVED


def reject_clip(
    conn: sqlite3.Connection,
    config: Config,
    clip_id: int,
    reason: str | None = None,
) -> str | None:
    """Reject a clip pending review, moving its file out of the review folder.

    Args:
        conn: Open database connection.
        config: Application configuration (locates the rejected folder).
        clip_id: Clip to reject.
        reason: Optional human-readable rejection reason.

    Returns:
        New clip status, or None if the clip is not pending review.

    Raises:
        ValueError: If the clip id does not exist.
    """
    clip = repo.get_clip(conn, clip_id)
    if clip is None:
        raise ValueError(f"Clip {clip_id} not found")
    if clip["status"] != CLIP_STATUS_REVIEW:
        return None

    src = Path(clip["path"])
    if src.exists():
        rejected_dir = config.get_path("rejected")
        rejected_dir.mkdir(parents=True, exist_ok=True)
        dst = rejected_dir / src.name
        moved = _move_clip_file(conn, clip_id, src, dst)
        if not moved:
            return None
    else:
        dst = src
        moved = False
        logger.warning("Clip %s file missing, rejecting without moving", clip_id)

    repo.update_clip_status(
        conn, clip_id, CLIP_STATUS_REJECTED, rejected_reason=reason, review_decision="rejected"
    )
    repo.add_event(
        conn,
        entity_type="clip",
        entity_id=clip_id,
        event_type=EVENT_CLIP_REJECTED,
        payload={"reason": reason, "path": str(dst), "moved": moved},
    )
    conn.commit()
    logger.info("Clip %s rejected", clip_id)
    return CLIP_STATUS_REJECTED
