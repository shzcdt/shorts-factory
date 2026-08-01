import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clip_pilot import db, repo
from clip_pilot.config import Config
from clip_pilot.formatter import format_clip, format_cut_clips


def make_config(root: Path) -> Config:
    return Config(
        {
            "paths": {
                "inbox": str(root / "inbox"),
                "staging": str(root / "staging"),
                "clips": str(root / "clips"),
                "review": str(root / "review"),
                "published": str(root / "published"),
                "logs": str(root / "logs"),
                "db": str(root / "db.sqlite3"),
            },
            "video": {
                "ffmpeg_path": "ffmpeg",
                "formatting": {"width": 1080, "height": 1920, "strategy": "center_crop"},
            },
            "watcher": {},
            "review": {"default_mode": "manual"},
        }
    )


class TestFormatter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root)
        self.conn = db.init_db(self.config.get_path("db"))
        self.config.ensure_dirs()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add_source_with_clip(
        self, *, file_hash: str = "h", start: float = 0.0, end: float = 30.0
    ) -> tuple[int, int]:
        source_path = self.root / "staging" / "video.mp4"
        source_path.write_bytes(b"fake-video")
        sid = repo.create_source(self.conn, file_path=str(source_path), file_hash=file_hash)
        cid = repo.create_clip(self.conn, source_id=sid, start_time=start, end_time=end)
        self.conn.commit()
        return sid, cid

    def test_format_marks_ready(self):
        sid, cid = self._add_source_with_clip()
        with mock.patch(
            "clip_pilot.formatter.ffmpeg_tools.cut_and_format", return_value=(True, None)
        ):
            status = format_clip(self.conn, cid, self.config)
        self.assertEqual(status, "ready")
        clip = repo.get_clip(self.conn, cid)
        self.assertEqual(clip["status"], "ready")
        self.assertTrue(Path(clip["path"]).name == f"{cid}.mp4")
        events = repo.get_events(self.conn, entity_type="clip", entity_id=cid)
        self.assertEqual(events[0]["event_type"], "clip_formatted")

    def test_format_skips_non_cut(self):
        sid, cid = self._add_source_with_clip()
        repo.update_clip_status(self.conn, cid, "ready")
        self.conn.commit()
        self.assertIsNone(format_clip(self.conn, cid, self.config))

    def test_format_missing_source_fails(self):
        sid, cid = self._add_source_with_clip()
        source_path = self.root / "staging" / "video.mp4"
        source_path.unlink()
        status = format_clip(self.conn, cid, self.config)
        self.assertEqual(status, "failed")
        clip = repo.get_clip(self.conn, cid)
        self.assertEqual(clip["status"], "failed")
        events = repo.get_events(self.conn, entity_type="clip", entity_id=cid)
        self.assertEqual(events[0]["event_type"], "clip_format_failed")

    def test_format_failure_sets_failed(self):
        sid, cid = self._add_source_with_clip()
        with mock.patch(
            "clip_pilot.formatter.ffmpeg_tools.cut_and_format", return_value=(False, "encode error")
        ):
            status = format_clip(self.conn, cid, self.config)
        self.assertEqual(status, "failed")
        clip = repo.get_clip(self.conn, cid)
        self.assertEqual(clip["status"], "failed")
        self.assertEqual(clip["rejected_reason"], "encode error")

    def test_format_cut_clips_respects_limit(self):
        sid, cid1 = self._add_source_with_clip(file_hash="h1")
        cid2 = repo.create_clip(self.conn, source_id=sid, start_time=1.0, end_time=10.0)
        self.conn.commit()
        with mock.patch(
            "clip_pilot.formatter.ffmpeg_tools.cut_and_format", return_value=(True, None)
        ):
            count = format_cut_clips(self.conn, self.config, limit=1)
        self.assertEqual(count, 1)
        self.assertEqual(repo.get_clip(self.conn, cid1)["status"], "ready")
        self.assertEqual(repo.get_clip(self.conn, cid2)["status"], "cut")

    def test_format_cut_clips_respects_source_id(self):
        sid1, cid1 = self._add_source_with_clip(file_hash="h1")
        sid2, cid2 = self._add_source_with_clip(file_hash="h2")
        with mock.patch(
            "clip_pilot.formatter.ffmpeg_tools.cut_and_format", return_value=(True, None)
        ):
            count = format_cut_clips(self.conn, self.config, source_id=sid1)
        self.assertEqual(count, 1)
        self.assertEqual(repo.get_clip(self.conn, cid1)["status"], "ready")
        self.assertEqual(repo.get_clip(self.conn, cid2)["status"], "cut")


if __name__ == "__main__":
    unittest.main()
