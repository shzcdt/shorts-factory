import tempfile
import unittest
from pathlib import Path

from clip_pilot import db, repo
from clip_pilot.config import Config
from clip_pilot.uploader import publish_approved_clips


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
            "watcher": {},
            "review": {"default_mode": "manual"},
            "playwright": {"headless": True, "timeout_seconds": 5, "slow_mo_ms": 0},
            "upload": {"max_videos_per_day": 5, "delay_between_seconds": [0, 0]},
        }
    )


class FakeUploader:
    def __init__(self):
        self.titles: list[str] = []
        self.raise_error: Exception | None = None

    def upload(self, *, video_path: Path, title: str) -> str:
        if self.raise_error is not None:
            raise self.raise_error
        self.titles.append(title)
        return f"vid-{video_path.stem}"


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root)
        self.conn = db.init_db(self.config.get_path("db"))
        self.config.ensure_dirs()
        self.uploader = FakeUploader()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add_source(self, *, file_hash: str = "h", title: str = "my_video") -> int:
        source_path = self.root / "staging" / f"{title}.mp4"
        source_path.write_bytes(b"fake-video")
        sid = repo.create_source(
            self.conn, file_path=str(source_path), file_hash=file_hash, title=title
        )
        self.conn.commit()
        return sid

    def _add_approved_clip(self, sid: int, *, start: float = 0.0) -> int:
        cid = repo.create_clip(self.conn, source_id=sid, start_time=start, end_time=start + 30)
        path = self.config.get_path("clips") / f"{cid}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-clip")
        repo.update_clip_path(self.conn, cid, str(path))
        repo.update_clip_status(self.conn, cid, "approved")
        self.conn.commit()
        return cid

    def _add_account(self, name: str = "main") -> int:
        account_id = repo.create_account(self.conn, name=name)
        session_path = self.config.get_path("auth") / f"{name}.json"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text("{}")
        self.conn.commit()
        return account_id

    def test_publish_success(self):
        self._add_account()
        sid = self._add_source()
        cid1 = self._add_approved_clip(sid, start=0)
        cid2 = self._add_approved_clip(sid, start=30)
        results = publish_approved_clips(
            self.conn, self.config, account_name="main", uploader=self.uploader
        )
        self.assertEqual(results["published"], 2)
        self.assertEqual(self.uploader.titles, ["my_video #1", "my_video #2"])
        for cid in (cid1, cid2):
            clip = repo.get_clip(self.conn, cid)
            self.assertEqual(clip["status"], "published")
            self.assertTrue(Path(clip["path"]).exists())
            self.assertTrue(str(self.config.get_path("published")) in clip["path"])
        account = repo.get_account_by_name(self.conn, "main")
        self.assertEqual(account["posts_today"], 2)
        posts = repo.get_posts_by_status(self.conn, "published")
        self.assertEqual(len(posts), 2)
        self.assertTrue(posts[0]["youtube_video_id"])

    def test_publish_failure_keeps_approved(self):
        self._add_account()
        sid = self._add_source()
        cid = self._add_approved_clip(sid)
        self.uploader.raise_error = RuntimeError("upload boom")
        results = publish_approved_clips(
            self.conn, self.config, account_name="main", uploader=self.uploader
        )
        self.assertEqual(results["failed"], 1)
        self.assertEqual(repo.get_clip(self.conn, cid)["status"], "approved")
        events = repo.get_events(self.conn, entity_type="clip", entity_id=cid)
        self.assertEqual(events[0]["event_type"], "clip_publish_failed")

    def test_publish_respects_daily_limit(self):
        self._add_account()
        self.config.upload = {"max_videos_per_day": 1, "delay_between_seconds": [0, 0]}
        sid = self._add_source()
        self._add_approved_clip(sid, start=0)
        self._add_approved_clip(sid, start=30)
        results = publish_approved_clips(
            self.conn, self.config, account_name="main", uploader=self.uploader
        )
        self.assertEqual(results["published"], 1)

    def test_publish_missing_account_raises(self):
        sid = self._add_source()
        self._add_approved_clip(sid)
        with self.assertRaises(ValueError):
            publish_approved_clips(
                self.conn, self.config, account_name="nope", uploader=self.uploader
            )

    def test_publish_missing_session_raises(self):
        repo.create_account(self.conn, name="main")
        self.conn.commit()
        sid = self._add_source()
        self._add_approved_clip(sid)
        with self.assertRaises(ValueError):
            publish_approved_clips(
                self.conn, self.config, account_name="main", uploader=self.uploader
            )

    def test_publish_missing_file_counts_failed(self):
        self._add_account()
        sid = self._add_source()
        cid = self._add_approved_clip(sid)
        Path(repo.get_clip(self.conn, cid)["path"]).unlink()
        results = publish_approved_clips(
            self.conn, self.config, account_name="main", uploader=self.uploader
        )
        self.assertEqual(results["failed"], 1)
        self.assertEqual(results["published"], 0)
        self.assertEqual(self.uploader.titles, [])


if __name__ == "__main__":
    unittest.main()
