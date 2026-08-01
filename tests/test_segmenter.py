import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clip_pilot import db, repo
from clip_pilot.config import Config
from clip_pilot.segmenter import segment_done_sources, segment_source


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
                "max_clip_seconds": 60.0,
                "min_clip_seconds": 15.0,
                "scene": {"detector": "content", "threshold": 27.0, "min_scene_seconds": 3.0},
            },
            "watcher": {},
            "review": {"default_mode": "manual"},
        }
    )


class TestSegmenter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root)
        self.conn = db.init_db(self.config.get_path("db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add_done_source(self, name: str = "video.mp4", file_hash: str = "h"):
        sid = repo.create_source(self.conn, file_path=str(self.root / name), file_hash=file_hash)
        repo.update_source_status(self.conn, sid, "done")
        self.conn.commit()
        return sid

    def test_segment_creates_clips(self):
        sid = self._add_done_source()
        scenes = [(0.0, 10.0), (10.0, 40.0), (40.0, 70.0)]
        with mock.patch("clip_pilot.segmenter.scenes.detect_scenes", return_value=scenes):
            status = segment_source(self.conn, sid, self.config)
        self.assertEqual(status, "segmented")
        source = repo.get_source(self.conn, sid)
        self.assertEqual(source["status"], "segmented")
        clips = repo.get_clips_by_source(self.conn, sid)
        self.assertEqual(len(clips), 3)
        self.assertEqual(clips[0]["start_time"], 0.0)
        self.assertEqual(clips[0]["end_time"], 10.0)
        self.assertEqual(clips[0]["status"], "cut")

    def test_segment_records_events(self):
        sid = self._add_done_source()
        with mock.patch("clip_pilot.segmenter.scenes.detect_scenes", return_value=[(0.0, 20.0)]):
            segment_source(self.conn, sid, self.config)
        source_events = repo.get_events(self.conn, entity_type="source", entity_id=sid)
        self.assertEqual(source_events[0]["event_type"], "segmented")
        clip_id = repo.get_clips_by_source(self.conn, sid)[0]["id"]
        clip_events = repo.get_events(self.conn, entity_type="clip", entity_id=clip_id)
        self.assertEqual(clip_events[0]["event_type"], "clip_created")

    def test_segment_skips_non_done(self):
        sid = repo.create_source(self.conn, file_path=str(self.root / "v.mp4"))
        self.conn.commit()
        self.assertIsNone(segment_source(self.conn, sid, self.config))

    def test_segment_skips_source_with_clips(self):
        sid = self._add_done_source()
        with mock.patch("clip_pilot.segmenter.scenes.detect_scenes", return_value=[(0.0, 20.0)]):
            segment_source(self.conn, sid, self.config)
        self.assertEqual(repo.count_clips_by_source(self.conn, sid), 1)
        self.assertIsNone(segment_source(self.conn, sid, self.config))
        self.assertEqual(repo.count_clips_by_source(self.conn, sid), 1)

    def test_segment_fails_on_no_scenes(self):
        sid = self._add_done_source()
        with mock.patch("clip_pilot.segmenter.scenes.detect_scenes", return_value=[]):
            status = segment_source(self.conn, sid, self.config)
        self.assertEqual(status, "failed")
        source = repo.get_source(self.conn, sid)
        self.assertEqual(source["status"], "failed")
        events = repo.get_events(self.conn, entity_type="source", entity_id=sid)
        self.assertEqual(events[0]["event_type"], "segment_failed")

    def test_segment_done_sources_batch(self):
        sid1 = self._add_done_source(file_hash="h1")
        sid2 = self._add_done_source(file_hash="h2")
        with mock.patch("clip_pilot.segmenter.scenes.detect_scenes", return_value=[(0.0, 25.0)]):
            count = segment_done_sources(self.conn, self.config)
        self.assertEqual(count, 2)
        self.assertEqual(repo.count_clips_by_source(self.conn, sid1), 1)
        self.assertEqual(repo.count_clips_by_source(self.conn, sid2), 1)


if __name__ == "__main__":
    unittest.main()
