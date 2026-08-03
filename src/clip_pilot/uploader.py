"""Publishing orchestration: turn approved clips into published YouTube posts.

The actual browser work is delegated to an :class:`Uploader` implementation
(Playwright by default), so the backend can be swapped later without touching
the pipeline. Title convention: ``<source title> #<clip number>`` where the
number is the clip's position among all clips of its source.
"""

import logging
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Protocol

from clip_pilot import repo
from clip_pilot.config import Config
from clip_pilot.constants import (
    CLIP_STATUS_APPROVED,
    CLIP_STATUS_PUBLISHED,
    EVENT_CLIP_PUBLISH_FAILED,
    EVENT_CLIP_PUBLISHED,
)

logger = logging.getLogger("clip_pilot.uploader")


class Uploader(Protocol):
    """Upload a single clip and return its YouTube video id."""

    def upload(self, *, video_path: Path, title: str) -> str: ...


def _clip_number(conn: sqlite3.Connection, clip: dict) -> int:
    """Return the 1-based position of a clip among its source's clips."""
    for index, sibling in enumerate(repo.get_clips_by_source(conn, clip["source_id"]), start=1):
        if sibling["id"] == clip["id"]:
            return index
    return 0


def _clip_title(conn: sqlite3.Connection, clip: dict) -> str:
    """Build the YouTube title for a clip: ``<source title> #<number>``."""
    source = repo.get_source(conn, clip["source_id"])
    if source is None:
        raise ValueError(f"Source for clip {clip['id']} not found")
    base = source["title"] or Path(source["file_path"]).stem
    return f"{base} #{_clip_number(conn, clip)}"


def publish_approved_clips(
    conn: sqlite3.Connection,
    config: Config,
    *,
    account_name: str,
    limit: int | None = None,
    uploader: Uploader | None = None,
) -> dict:
    """Publish approved clips to YouTube through an Uploader.

    Args:
        conn: Open database connection.
        config: Application configuration.
        account_name: Name of the account to publish as.
        limit: Maximum number of clips to attempt.
        uploader: Uploader backend; defaults to the Playwright one.

    Returns:
        Dict with ``published`` and ``failed`` counts plus per-clip errors.

    Raises:
        ValueError: If the account or its saved session does not exist.
    """
    account = repo.get_account_by_name(conn, account_name)
    if account is None:
        raise ValueError(
            f"Account {account_name!r} not found. Run 'auth login --name {account_name}' first."
        )
    if account["status"] != "active":
        raise ValueError(f"Account {account_name!r} is not active")

    session_path = config.get_path("auth") / f"{account_name}.json"
    if not session_path.exists():
        raise ValueError(
            f"Session for {account_name!r} not found. Run 'auth login --name {account_name}' first."
        )

    max_per_day = int(config.upload.get("max_videos_per_day", 5) or 5)
    if repo.count_posts_today(conn, account["id"]) >= max_per_day:
        logger.warning("Daily publish limit already reached (%s)", max_per_day)
        return {"published": 0, "failed": 0, "errors": []}

    clips = repo.get_clips_by_status(conn, CLIP_STATUS_APPROVED)
    if limit is not None:
        clips = clips[:limit]

    if uploader is None:
        from clip_pilot.playwright_uploader import PlaywrightUploader

        uploader = PlaywrightUploader(config, account_name=account_name)

    published_dir = config.get_path("published")
    published_dir.mkdir(parents=True, exist_ok=True)
    delay_range = config.upload.get("delay_between_seconds", [45, 120])

    results: dict = {"published": 0, "failed": 0, "errors": []}
    account_id = account["id"]
    for position, clip in enumerate(clips):
        if repo.count_posts_today(conn, account_id) >= max_per_day:
            logger.warning("Daily publish limit reached (%s), stopping", max_per_day)
            break

        clip_id = clip["id"]
        video_path = Path(clip["path"])
        if not video_path.exists():
            logger.error("Clip %s file missing %s", clip_id, video_path)
            results["failed"] += 1
            results["errors"].append({"clip_id": clip_id, "error": "file_missing"})
            continue

        try:
            title = _clip_title(conn, clip)
            video_id = uploader.upload(video_path=video_path, title=title)
        except Exception as exc:
            logger.exception("Clip %s publish failed: %s", clip_id, exc)
            repo.add_event(
                conn,
                entity_type="clip",
                entity_id=clip_id,
                event_type=EVENT_CLIP_PUBLISH_FAILED,
                payload={"error": str(exc)},
            )
            conn.commit()
            results["failed"] += 1
            results["errors"].append({"clip_id": clip_id, "error": str(exc)})
            continue

        dst = published_dir / video_path.name
        try:
            os.replace(video_path, dst)
        except OSError:
            dst = video_path

        repo.update_clip_path(conn, clip_id, str(dst))
        repo.update_clip_status(conn, clip_id, CLIP_STATUS_PUBLISHED)
        repo.create_published_post(
            conn,
            clip_id=clip_id,
            account_id=account_id,
            youtube_video_id=video_id,
            url=f"https://youtu.be/{video_id}",
        )
        repo.update_account_after_publish(conn, account_id)
        repo.add_event(
            conn,
            entity_type="clip",
            entity_id=clip_id,
            event_type=EVENT_CLIP_PUBLISHED,
            payload={"video_id": video_id, "title": title},
        )
        conn.commit()
        logger.info("Clip %s published: %s (%s)", clip_id, video_id, title)
        results["published"] += 1

        if position < len(clips) - 1:
            low, high = delay_range[0], delay_range[1]
            delay = random.uniform(float(low), float(high))
            logger.info("Waiting %.0fs before the next upload", delay)
            time.sleep(delay)

    return results
