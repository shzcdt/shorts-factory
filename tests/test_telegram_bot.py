import tempfile
import unittest
from pathlib import Path

from clip_pilot import db, repo
from clip_pilot.config import Config
from clip_pilot.constants import CLIP_STATUS_APPROVED, CLIP_STATUS_REVIEW
from clip_pilot.telegram_bot import (
    _accounts_text,
    _clip_caption,
    _clips_text,
    _safe_filename,
    _save_incoming_video,
    _save_session_file,
    _sources_text,
    _status_text,
    format_help,
)


def make_config(root: Path) -> Config:
    return Config(
        {
            "paths": {
                "inbox": str(root / "inbox"),
                "staging": str(root / "staging"),
                "clips": str(root / "clips"),
                "review": str(root / "review"),
                "rejected": str(root / "rejected"),
                "published": str(root / "published"),
                "logs": str(root / "logs"),
                "auth": str(root / "auth"),
                "db": str(root / "db.sqlite3"),
            },
            "video": {},
            "watcher": {"extensions": [".mp4", ".mov"]},
            "review": {"default_mode": "manual"},
            "telegram": {"allowed_users": [1], "max_inbox_mb": 20},
            "playwright": {},
            "upload": {"max_videos_per_day": 5},
        }
    )


class TestTelegramBotHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root)
        self.config.ensure_dirs()
        self.conn = db.init_db(self.config.get_path("db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_safe_filename_strips_paths(self):
        self.assertEqual(_safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(_safe_filename("auth/main.json"), "main.json")

    def test_save_session_file(self):
        target = _save_session_file(self.config, "main.json", b"{}")
        self.assertEqual(target.read_bytes(), b"{}")
        with self.assertRaises(ValueError):
            _save_session_file(self.config, "main.txt", b"{}")

    def test_save_incoming_video_and_reject_non_video(self):
        saved = _save_incoming_video(self.config, "clip.mp4", b"v")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.parent.name, "inbox")
        self.assertIsNone(_save_incoming_video(self.config, "notes.txt", b"x"))

    def test_status_and_lists(self):
        source_id = repo.create_source(
            self.conn, file_path="x.mp4", title="interview", file_hash="h1"
        )
        clip_id = repo.create_clip(self.conn, source_id=source_id, start_time=75.0, end_time=120.0)
        repo.update_clip_path(self.conn, clip_id, "clip.mp4")
        repo.update_clip_status(self.conn, clip_id, CLIP_STATUS_REVIEW)

        status = _status_text(self.conn, self.config)
        self.assertIn("Источники", status)
        self.assertIn("Лимит", status)

        sources = _sources_text(self.conn)
        self.assertIn("interview", sources)

        clips = _clips_text(self.conn, source_id)
        self.assertIn("75-120с", clips)

        repo.update_clip_status(self.conn, clip_id, CLIP_STATUS_APPROVED)
        accounts = _accounts_text(self.conn, self.config)
        self.assertIn("Аккаунтов пока нет", accounts)

    def test_clip_caption_formatting(self):
        source_id = repo.create_source(
            self.conn, file_path="x.mp4", title="podcast", file_hash="h2"
        )
        clip_id = repo.create_clip(self.conn, source_id=source_id, start_time=75.0, end_time=135.0)
        clip = repo.get_clip(self.conn, clip_id)
        caption = _clip_caption(self.conn, clip, position=1, total=3)
        self.assertIn("podcast", caption)
        self.assertIn("1:15", caption)
        self.assertIn("60с", caption)
        self.assertIn("1 из 3", caption)

    def test_allowed_users_settings_roundtrip(self):
        from clip_pilot.telegram_bot import (
            _add_allowed_id,
            _allowed_ids,
            _remove_allowed_id,
        )

        base = _allowed_ids(self.conn, self.config)
        self.assertEqual(base, {1})
        _add_allowed_id(self.conn, 42)
        self.conn.commit()
        self.assertEqual(_allowed_ids(self.conn, self.config), {1, 42})
        _remove_allowed_id(self.conn, 42)
        self.conn.commit()
        self.assertEqual(_allowed_ids(self.conn, self.config), {1})

    def test_help_contains_roadmap(self):
        text = format_help()
        self.assertIn("роадмап", text.lower())
        self.assertIn("/adduser", text)


if __name__ == "__main__":
    unittest.main()
