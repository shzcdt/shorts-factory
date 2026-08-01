import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clip_pilot.probe import parse_fps, parse_resolution, probe_file


class TestParseFps(unittest.TestCase):
    def test_common_rates(self):
        self.assertAlmostEqual(parse_fps("30000/1001"), 29.97, places=2)
        self.assertEqual(parse_fps("30/1"), 30.0)
        self.assertEqual(parse_fps("25/1"), 25.0)
        self.assertAlmostEqual(parse_fps("60000/1001"), 59.94, places=2)

    def test_plain_number(self):
        self.assertEqual(parse_fps("60"), 60.0)

    def test_invalid(self):
        self.assertIsNone(parse_fps("0/0"))
        self.assertIsNone(parse_fps("abc"))
        self.assertIsNone(parse_fps(None))


class TestParseResolution(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(parse_resolution(1920, 1080), "1920x1080")

    def test_missing(self):
        self.assertIsNone(parse_resolution(None, 1080))
        self.assertIsNone(parse_resolution(1920, None))


class TestProbeFile(unittest.TestCase):
    def _ffprobe_json(self):
        return json.dumps(
            {
                "format": {"duration": "100.0"},
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "r_frame_rate": "30000/1001",
                    }
                ],
            }
        )

    def test_parses_metadata(self):
        with mock.patch(
            "clip_pilot.probe.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=self._ffprobe_json(), stderr=""),
        ):
            meta = probe_file(Path("v.mp4"))
        self.assertEqual(meta["duration_seconds"], 100.0)
        self.assertEqual(meta["resolution"], "1920x1080")
        self.assertAlmostEqual(meta["fps"], 29.97, places=2)

    def test_nonzero_returncode(self):
        with mock.patch(
            "clip_pilot.probe.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom"),
        ):
            self.assertIsNone(probe_file(Path("v.mp4")))

    def test_missing_binary(self):
        with mock.patch("clip_pilot.probe.subprocess.run", side_effect=OSError("no such file")):
            self.assertIsNone(probe_file(Path("v.mp4")))


if __name__ == "__main__":
    unittest.main()
