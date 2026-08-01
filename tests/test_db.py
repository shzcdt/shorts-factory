import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clip_pilot import db, repo


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite"
        self.conn = db.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_migrations_applied(self):
        self.assertEqual(db.current_version(self.conn), 1)

    def test_wal_enabled(self):
        mode = self.conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode, "wal")

    def test_create_source_and_find(self):
        sid = repo.create_source(self.conn, file_path="C:/video.mp4", file_hash="abc")
        self.conn.commit()
        found = repo.find_source_by_hash(self.conn, "abc")
        self.assertIsNotNone(found)
        self.assertEqual(found["file_path"], "C:/video.mp4")
        self.assertEqual(repo.get_source(self.conn, sid)["status"], "new")

    def test_file_hash_unique(self):
        repo.create_source(self.conn, file_path="a.mp4", file_hash="same")
        with self.assertRaises(sqlite3.IntegrityError):
            repo.create_source(self.conn, file_path="b.mp4", file_hash="same")

    def test_clip_flow(self):
        sid = repo.create_source(self.conn, file_path="v.mp4", file_hash="h1")
        cid = repo.create_clip(
            self.conn,
            source_id=sid,
            start_time=0,
            end_time=60,
            suggested_title="Test",
            hashtags=["#shorts"],
        )
        self.conn.commit()
        clips = repo.get_clips_by_status(self.conn, "cut")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["hashtags"], ["#shorts"])
        repo.update_clip_status(self.conn, cid, "rejected", rejected_reason="дубль")
        self.conn.commit()
        self.assertEqual(len(repo.get_clips_by_status(self.conn, "rejected")), 1)
        self.assertEqual(repo.get_clips_by_status(self.conn, "cut"), [])

    def test_clips_for_review_multiple_statuses(self):
        sid = repo.create_source(self.conn, file_path="v.mp4", file_hash="h3")
        repo.create_clip(self.conn, source_id=sid, start_time=0, end_time=10)
        c2 = repo.create_clip(self.conn, source_id=sid, start_time=10, end_time=20)
        repo.update_clip_status(self.conn, c2, "review")
        self.conn.commit()
        clips = repo.get_clips_for_review(self.conn, ["cut", "review"])
        self.assertEqual(len(clips), 2)
        self.assertEqual({c["status"] for c in clips}, {"cut", "review"})

    def test_accounts_and_posts(self):
        sid = repo.create_source(self.conn, file_path="v.mp4", file_hash="h2")
        aid = repo.create_account(self.conn, name="Main", channel_id="UC_1")
        cid = repo.create_clip(self.conn, source_id=sid, start_time=0, end_time=30)
        pid = repo.create_post(self.conn, clip_id=cid, account_id=aid)
        self.conn.commit()
        repo.update_post_status(self.conn, pid, "published")
        self.conn.commit()
        posts = repo.get_posts_by_status(self.conn, "published")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["attempts"], 1)

    def test_events(self):
        repo.add_event(
            self.conn, entity_type="source", entity_id=1, event_type="created", payload={"k": "v"}
        )
        self.conn.commit()
        events = repo.get_events(self.conn, entity_type="source")
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]["payload"]), {"k": "v"})

    def test_settings(self):
        repo.set_setting(self.conn, "autopilot", "on")
        self.conn.commit()
        self.assertEqual(repo.get_setting(self.conn, "autopilot"), "on")
        self.assertIsNone(repo.get_setting(self.conn, "nope"))


if __name__ == "__main__":
    unittest.main()
