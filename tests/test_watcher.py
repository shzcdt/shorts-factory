import tempfile
import unittest
from pathlib import Path

from clip_pilot import db, repo
from clip_pilot.config import Config
from clip_pilot.watcher import detect_video_files, is_file_settled, scan_inbox


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
            "watcher": {
                "scan_interval_seconds": 1,
                "settle_seconds": 0.2,
                "hash_chunk_mb": 1,
                "extensions": [".mp4", ".mov"],
            },
            "review": {"default_mode": "manual"},
        }
    )


class TestWatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root)
        self.inbox = self.config.get_path("inbox")
        self.inbox.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect_video_files_filters_extensions(self):
        (self.inbox / "a.mp4").write_bytes(b"x")
        (self.inbox / "b.txt").write_bytes(b"x")
        (self.inbox / "c.mov").write_bytes(b"x")
        found = detect_video_files(self.inbox, (".mp4", ".mov"))
        self.assertEqual({p.name for p in found}, {"a.mp4", "c.mov"})

    def test_is_file_settled_stable(self):
        path = self.inbox / "a.mp4"
        path.write_bytes(b"x" * 100)
        self.assertTrue(is_file_settled(path, settle_seconds=0.2, check_interval=0.1))

    def test_is_file_settled_deleted(self):
        path = self.inbox / "a.mp4"
        path.write_bytes(b"x")
        path.unlink()
        self.assertFalse(is_file_settled(path, settle_seconds=0.2, check_interval=0.1))

    def test_scan_inbox_ingests_and_moves(self):
        conn = db.init_db(self.config.get_path("db"))
        video = self.inbox / "clip.mp4"
        video.write_bytes(b"x" * 100)
        count = scan_inbox(conn, self.config)
        sources = repo.get_sources_by_status(conn, "new")
        conn.close()
        self.assertEqual(count, 1)
        self.assertFalse(video.exists())
        self.assertEqual(len(sources), 1)
        self.assertEqual(
            Path(sources[0]["file_path"]), self.config.get_path("staging") / "clip.mp4"
        )


if __name__ == "__main__":
    unittest.main()
