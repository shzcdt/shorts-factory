import tempfile
import unittest
from pathlib import Path

from clip_pilot import db, repo
from clip_pilot.config import Config
from clip_pilot.review import (
    _review_filename,
    _sanitize_stem,
    approve_all,
    approve_clip,
    list_clips_for_review,
    prepare_for_review,
    reject_all,
    reject_clip,
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
                "db": str(root / "db.sqlite3"),
            },
            "video": {},
            "watcher": {},
            "review": {"default_mode": "manual"},
        }
    )


class TestReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root)
        self.conn = db.init_db(self.config.get_path("db"))
        self.config.ensure_dirs()

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

    def _add_clip(
        self,
        sid: int,
        *,
        status: str = "ready",
        start: float = 5.0,
        end: float = 20.0,
        with_file: bool = True,
    ) -> int:
        cid = repo.create_clip(self.conn, source_id=sid, start_time=start, end_time=end)
        if with_file:
            path = self.config.get_path("clips") / f"{cid}.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-clip")
            repo.update_clip_path(self.conn, cid, str(path))
        repo.update_clip_status(self.conn, cid, status)
        self.conn.commit()
        return cid

    def _add_review_clip(self, sid: int, **kwargs) -> int:
        cid = repo.create_clip(
            self.conn,
            source_id=sid,
            start_time=kwargs.get("start", 5.0),
            end_time=kwargs.get("end", 20.0),
        )
        path = self.config.get_path("review") / f"{cid}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-clip")
        repo.update_clip_path(self.conn, cid, str(path))
        repo.update_clip_status(self.conn, cid, "review")
        self.conn.commit()
        return cid

    def test_sanitize_stem(self):
        self.assertEqual(_sanitize_stem('a<b>:"c'), "a_b___c")
        self.assertEqual(_sanitize_stem("..  .."), "clip")
        self.assertEqual(_sanitize_stem("x" * 100), "x" * 30)

    def test_review_filename(self):
        name = _review_filename(str(self.root / "staging" / "podcast_ep3.mp4"), 45.7, 7)
        self.assertEqual(name, "podcast_ep3_45s_7.mp4")

    def test_prepare_moves_ready_to_review(self):
        sid = self._add_source()
        cid = self._add_clip(sid)
        count = prepare_for_review(self.conn, self.config)
        self.assertEqual(count, 1)
        clip = repo.get_clip(self.conn, cid)
        self.assertEqual(clip["status"], "review")
        self.assertEqual(clip["path"], str(self.config.get_path("review") / "my_video_5s_1.mp4"))
        self.assertTrue(Path(clip["path"]).exists())
        events = repo.get_events(self.conn, entity_type="clip", entity_id=cid)
        self.assertEqual(events[0]["event_type"], "clip_review_prepared")

    def test_prepare_skips_non_ready(self):
        sid = self._add_source()
        self._add_clip(sid, status="cut")
        self.assertEqual(prepare_for_review(self.conn, self.config), 0)

    def test_prepare_idempotent(self):
        sid = self._add_source()
        self._add_clip(sid)
        first = prepare_for_review(self.conn, self.config)
        second = prepare_for_review(self.conn, self.config)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)

    def test_prepare_missing_file_fails(self):
        sid = self._add_source()
        cid = self._add_clip(sid, with_file=False)
        count = prepare_for_review(self.conn, self.config)
        self.assertEqual(count, 0)
        self.assertEqual(repo.get_clip(self.conn, cid)["status"], "failed")
        events = repo.get_events(self.conn, entity_type="clip", entity_id=cid)
        self.assertEqual(events[0]["event_type"], "clip_review_prepared")

    def test_list_review(self):
        sid = self._add_source()
        self._add_review_clip(sid)
        self.assertEqual(len(list_clips_for_review(self.conn)), 1)

    def test_approve(self):
        sid = self._add_source()
        cid = self._add_review_clip(sid)
        status = approve_clip(self.conn, cid)
        self.assertEqual(status, "approved")
        clip = repo.get_clip(self.conn, cid)
        self.assertEqual(clip["status"], "approved")
        self.assertEqual(clip["review_decision"], "approved")
        events = repo.get_events(self.conn, entity_type="clip", entity_id=cid)
        self.assertEqual(events[0]["event_type"], "clip_approved")

    def test_approve_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            approve_clip(self.conn, 999)

    def test_approve_wrong_status_returns_none(self):
        sid = self._add_source()
        cid = self._add_clip(sid, status="ready")
        self.assertIsNone(approve_clip(self.conn, cid))

    def test_reject_moves_file(self):
        sid = self._add_source()
        cid = self._add_review_clip(sid)
        status = reject_clip(self.conn, self.config, cid, reason="too boring")
        self.assertEqual(status, "rejected")
        clip = repo.get_clip(self.conn, cid)
        self.assertEqual(clip["status"], "rejected")
        self.assertEqual(clip["rejected_reason"], "too boring")
        self.assertTrue(Path(clip["path"]).exists())
        self.assertFalse((self.config.get_path("review") / f"{cid}.mp4").exists())
        events = repo.get_events(self.conn, entity_type="clip", entity_id=cid)
        self.assertEqual(events[0]["event_type"], "clip_rejected")

    def test_reject_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            reject_clip(self.conn, self.config, 999)

    def test_reject_wrong_status_returns_none(self):
        sid = self._add_source()
        cid = self._add_clip(sid, status="ready")
        self.assertIsNone(reject_clip(self.conn, self.config, cid))

    def test_reject_missing_file_still_rejected(self):
        sid = self._add_source()
        cid = self._add_review_clip(sid)
        Path(repo.get_clip(self.conn, cid)["path"]).unlink()
        status = reject_clip(self.conn, self.config, cid)
        self.assertEqual(status, "rejected")
        self.assertEqual(repo.get_clip(self.conn, cid)["status"], "rejected")

    def test_approve_all(self):
        sid = self._add_source()
        cid1 = self._add_review_clip(sid)
        cid2 = self._add_review_clip(sid)
        count = approve_all(self.conn)
        self.assertEqual(count, 2)
        self.assertEqual(repo.get_clip(self.conn, cid1)["status"], "approved")
        self.assertEqual(repo.get_clip(self.conn, cid2)["status"], "approved")

    def test_reject_all_moves_files(self):
        sid = self._add_source()
        cid1 = self._add_review_clip(sid)
        cid2 = self._add_review_clip(sid)
        count = reject_all(self.conn, self.config, reason="all bad")
        self.assertEqual(count, 2)
        self.assertEqual(repo.get_clip(self.conn, cid1)["status"], "rejected")
        self.assertEqual(repo.get_clip(self.conn, cid2)["status"], "rejected")
        self.assertEqual(repo.get_clip(self.conn, cid1)["rejected_reason"], "all bad")
        self.assertFalse((self.config.get_path("review") / f"{cid1}.mp4").exists())
        self.assertFalse((self.config.get_path("review") / f"{cid2}.mp4").exists())


if __name__ == "__main__":
    unittest.main()
