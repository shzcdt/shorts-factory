"""Repository functions: all database access for pipeline entities.

Every function receives a connection as the first argument so callers
control transaction boundaries (avoiding "database is locked").
"""

import json
import sqlite3


def _deserialize_clip(row: sqlite3.Row) -> dict:
    """Convert a clip row to a dict, parsing JSON fields."""
    data = dict(row)
    for key in ("hashtags", "crop_box"):
        raw = data.get(key)
        data[key] = json.loads(raw) if raw else None
    return data


def create_source(conn: sqlite3.Connection, *, file_path: str, source_type: str = "file",
                  title: str | None = None, file_hash: str | None = None,
                  language: str = "ru", review_mode: str = "manual") -> int:
    """Create a new source and return its id."""
    cur = conn.execute(
        "INSERT INTO sources (file_path, source_type, title, file_hash, language, review_mode) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (file_path, source_type, title, file_hash, language, review_mode),
    )
    return cur.lastrowid


def find_source_by_hash(conn: sqlite3.Connection, file_hash: str) -> dict | None:
    """Return a source by its file hash, or None if not found."""
    row = conn.execute("SELECT * FROM sources WHERE file_hash = ?", (file_hash,)).fetchone()
    return dict(row) if row else None


def get_source(conn: sqlite3.Connection, source_id: int) -> dict | None:
    """Return a source by id, or None if not found."""
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return dict(row) if row else None


def update_source_status(conn: sqlite3.Connection, source_id: int, status: str,
                         error: str | None = None) -> None:
    """Update a source status and optionally record the error."""
    conn.execute(
        "UPDATE sources SET status = ?, error = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, error, source_id),
    )


def create_clip(conn: sqlite3.Connection, *, source_id: int, start_time: float, end_time: float,
                status: str = "cut", score: float | None = None, suggested_title: str | None = None,
                suggested_description: str | None = None, hashtags: list[str] | None = None,
                transcript_excerpt: str | None = None, crop_box: dict | None = None) -> int:
    """Create a clip and return its id. JSON fields are serialized internally."""
    cur = conn.execute(
        "INSERT INTO clips (source_id, start_time, end_time, status, score, suggested_title, "
        "suggested_description, hashtags, transcript_excerpt, crop_box) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (source_id, start_time, end_time, status, score, suggested_title, suggested_description,
         json.dumps(hashtags, ensure_ascii=False) if hashtags else None,
         transcript_excerpt,
         json.dumps(crop_box) if crop_box else None),
    )
    return cur.lastrowid


def get_clip(conn: sqlite3.Connection, clip_id: int) -> dict | None:
    """Return a clip by id, or None if not found."""
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _deserialize_clip(row) if row else None


def get_clips_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    """Return all clips in a given status, ordered by creation time."""
    rows = conn.execute(
        "SELECT * FROM clips WHERE status = ? ORDER BY created_at", (status,)
    ).fetchall()
    return [_deserialize_clip(r) for r in rows]


def get_clips_for_review(conn: sqlite3.Connection, statuses: list[str]) -> list[dict]:
    """Return clips matching any of the given statuses, ordered by creation time."""
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT * FROM clips WHERE status IN ({placeholders}) ORDER BY created_at", statuses
    ).fetchall()
    return [_deserialize_clip(r) for r in rows]


def update_clip_status(conn: sqlite3.Connection, clip_id: int, status: str,
                       rejected_reason: str | None = None,
                       review_decision: str | None = None) -> None:
    """Update a clip status and optional rejection metadata."""
    conn.execute(
        "UPDATE clips SET status = ?, rejected_reason = ?, review_decision = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, rejected_reason, review_decision, clip_id),
    )


def create_account(conn: sqlite3.Connection, *, name: str, channel_id: str | None = None,
                   refresh_token: str | None = None, daily_post_limit: int = 5) -> int:
    """Create a publishing account and return its id."""
    cur = conn.execute(
        "INSERT INTO accounts (name, channel_id, refresh_token, daily_post_limit) VALUES (?, ?, ?, ?)",
        (name, channel_id, refresh_token, daily_post_limit),
    )
    return cur.lastrowid


def get_accounts(conn: sqlite3.Connection, status: str = "active") -> list[dict]:
    """Return all accounts in a given status."""
    rows = conn.execute("SELECT * FROM accounts WHERE status = ?", (status,)).fetchall()
    return [dict(r) for r in rows]


def create_post(conn: sqlite3.Connection, *, clip_id: int, account_id: int,
                scheduled_at: str | None = None) -> int:
    """Create a post record and return its id."""
    cur = conn.execute(
        "INSERT INTO posts (clip_id, account_id, scheduled_at) VALUES (?, ?, ?)",
        (clip_id, account_id, scheduled_at),
    )
    return cur.lastrowid


def update_post_status(conn: sqlite3.Connection, post_id: int, status: str,
                       error: str | None = None) -> None:
    """Update a post status, incrementing the attempt counter."""
    conn.execute(
        "UPDATE posts SET status = ?, error = ?, attempts = attempts + 1, "
        "last_attempt_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, error, post_id),
    )


def get_posts_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    """Return all posts in a given status, ordered by creation time."""
    rows = conn.execute(
        "SELECT * FROM posts WHERE status = ? ORDER BY created_at", (status,)
    ).fetchall()
    return [dict(r) for r in rows]


def add_event(conn: sqlite3.Connection, *, entity_type: str, entity_id: int | None,
              event_type: str, payload: dict | None = None) -> None:
    """Append an audit event for a pipeline entity."""
    conn.execute(
        "INSERT INTO events (entity_type, entity_id, event_type, payload) VALUES (?, ?, ?, ?)",
        (entity_type, entity_id, event_type,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None),
    )


def get_events(conn: sqlite3.Connection, *, entity_type: str | None = None,
               entity_id: int | None = None, limit: int = 100) -> list[dict]:
    """Return audit events, optionally filtered by entity, newest first."""
    query = "SELECT * FROM events"
    where = []
    params: list = []
    if entity_type is not None:
        where.append("entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        where.append("entity_id = ?")
        params.append(entity_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a settings key/value pair."""
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (key, value),
    )


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Return a setting value or the default if the key is absent."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
