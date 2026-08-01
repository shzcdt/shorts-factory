import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clip_pilot import db, repo
from clip_pilot.analyzer import analyze_new_sources, analyze_source, retry_source
from clip_pilot.config import Config


def make_config(root: Path, min_clip_seconds: float = 15.0) -> Config:
    return Config({
        "paths": {
            "inbox": str(root / "inbox"),
            "staging": str(root / "staging"),
            "clips": str(root / "clips"),
            "review": str(root / "review"),
            "published": str(root / "published"),
            "logs": str(root / "logs"),
            "db": str(root / "db.sqlite3"),
        },
        "video": {"min_clip_seconds": min_clip_seconds, "ffprobe_path": "ffprobe"},
        "watcher": {},
        "review": {"default_mode": "manual"},
    })


class TestAnalyzer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root)
        self.conn = db.init_db(self.config.get_path("db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add_source(self, name: str = "video.mp4", file_hash: str = "h"):
        return repo.create_source(self.conn, file_path=str(self.root / name), file_hash=file_hash)

    def test_analyze_marks_done(self):
        sid = self._add_source()
        metadata = {"duration_seconds": 120.0, "resolution": "1920x1080", "fps": 30.0,
                    "metadata_json": {"format": {"duration": "120.0"}}}
        with mock.patch("clip_pilot.analyzer.probe_file", return_value=metadata):
            status = analyze_source(self.conn, sid, self.config)
        self.assertEqual(status, "done")
        source = repo.get_source(self.conn, sid)
        self.assertEqual(source["duration_seconds"], 120.0)
        self.assertEqual(source["resolution"], "1920x1080")
        self.assertEqual(source["fps"], 30.0)
        self.assertIsNotNone(source["metadata_json"])

    def test_analyze_skips_too_short(self):
        sid = self._add_source()
        metadata = {"duration_seconds": 5.0, "resolution": "1920x1080", "fps": 30.0,
                    "metadata_json": {}}
        with mock.patch("clip_pilot.analyzer.probe_file", return_value=metadata):
            status = analyze_source(self.conn, sid, self.config)
        self.assertEqual(status, "skipped")
        events = repo.get_events(self.conn, entity_type="source", entity_id=sid)
        self.assertEqual(events[0]["event_type"], "skipped_too_short")

    def test_analyze_failed_on_probe_error(self):
        sid = self._add_source()
        with mock.patch("clip_pilot.analyzer.probe_file", return_value=None):
            status = analyze_source(self.conn, sid, self.config)
        self.assertEqual(status, "failed")
        events = repo.get_events(self.conn, entity_type="source", entity_id=sid)
        self.assertEqual(events[0]["event_type"], "analyze_failed")

    def test_analyze_ignores_non_new(self):
        sid = self._add_source()
        repo.update_source_status(self.conn, sid, "done")
        self.conn.commit()
        self.assertIsNone(analyze_source(self.conn, sid, self.config))

    def test_analyze_new_sources_count(self):
        self._add_source(file_hash="h1")
        self._add_source(file_hash="h2")
        metadata = {"duration_seconds": 60.0, "resolution": "1080x1920", "fps": 30.0,
                    "metadata_json": {}}
        with mock.patch("clip_pilot.analyzer.probe_file", return_value=metadata):
            count = analyze_new_sources(self.conn, self.config)
        self.assertEqual(count, 2)

    def test_retry_resets_skipped(self):
        sid = self._add_source()
        repo.update_source_status(self.conn, sid, "skipped", error="short")
        self.conn.commit()
        self.assertTrue(retry_source(self.conn, sid))
        self.assertEqual(repo.get_source(self.conn, sid)["status"], "new")

    def test_retry_rejects_done(self):
        sid = self._add_source()
        repo.update_source_status(self.conn, sid, "done")
        self.conn.commit()
        self.assertFalse(retry_source(self.conn, sid))

    def test_analyze_integration_with_real_ffmpeg(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not available")
        video = self.root / "real.mp4"
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1",
               "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
               "-t", "1", "-pix_fmt", "yuv420p", str(video)]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            self.skipTest("ffmpeg could not generate a test video")
        sid = repo.create_source(self.conn, file_path=str(video), file_hash="real")
        status = analyze_source(self.conn, sid, make_config(self.root, min_clip_seconds=0.5))
        self.assertEqual(status, "done")
        source = repo.get_source(self.conn, sid)
        self.assertGreater(source["duration_seconds"], 0.5)
        self.assertIsNotNone(source["resolution"])
        self.assertIsNotNone(source["fps"])


if __name__ == "__main__":
    unittest.main()
