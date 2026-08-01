import tempfile
import unittest
from pathlib import Path

from clip_pilot import db, repo
from clip_pilot.ingest import compute_file_hash, ingest_file


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite"
        self.conn = db.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _make_video(self, name: str = "video.mp4", size: int = 1024) -> Path:
        path = Path(self.tmp.name) / name
        path.write_bytes(b"x" * size)
        return path

    def test_compute_file_hash_stable(self):
        path = self._make_video()
        self.assertEqual(compute_file_hash(path), compute_file_hash(path))

    def test_ingest_creates_source(self):
        path = self._make_video()
        source_id = ingest_file(self.conn, path)
        self.assertIsNotNone(source_id)
        source = repo.get_source(self.conn, source_id)
        self.assertEqual(source["status"], "new")
        self.assertEqual(source["file_path"], str(path))
        self.assertEqual(source["title"], "video")

    def test_ingest_skips_duplicate(self):
        path = self._make_video()
        first = ingest_file(self.conn, path)
        second = ingest_file(self.conn, path)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        events = repo.get_events(self.conn, entity_type="source", entity_id=first)
        self.assertEqual(events[0]["event_type"], "duplicate_skipped")

    def test_ingest_missing_file_returns_none(self):
        path = Path(self.tmp.name) / "missing.mp4"
        self.assertIsNone(ingest_file(self.conn, path))


if __name__ == "__main__":
    unittest.main()
