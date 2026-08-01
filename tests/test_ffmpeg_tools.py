import unittest
from pathlib import Path
from unittest import mock

from clip_pilot.ffmpeg_tools import build_filter, cut_and_format


class TestBuildFilter(unittest.TestCase):
    def test_center_crop_contains_crop(self):
        f = build_filter(0.0, 10.0)
        self.assertIn("crop=w=1080:h=1920", f)
        self.assertIn("scale=w=1080:h=1920", f)
        self.assertIn("fps=30", f)

    def test_center_crop_not_blur(self):
        f = build_filter(0.0, 10.0)
        self.assertNotIn("boxblur", f)


class TestCutAndFormat(unittest.TestCase):
    def test_missing_source_returns_error(self):
        source = mock.Mock()
        source.exists.return_value = False
        output = mock.Mock()
        output.with_name.return_value = output
        ok, error = cut_and_format(source, output, start=0.0, end=5.0)
        self.assertFalse(ok)
        self.assertIn("missing", error or "")

    def test_success_replaces_tmp(self):
        source = mock.Mock()
        source.exists.return_value = True
        output = Path("out.mp4")
        tmp_path = Path("out.mp4.tmp")

        result = mock.Mock()
        result.returncode = 0
        result.stderr = ""
        with (
            mock.patch("clip_pilot.ffmpeg_tools.os.replace") as replace,
            mock.patch("clip_pilot.ffmpeg_tools.subprocess.run", return_value=result),
        ):
            ok, error = cut_and_format(source, output, start=0.0, end=5.0, ffmpeg_path="ffmpeg")
        self.assertTrue(ok)
        self.assertIsNone(error)
        replace.assert_called_once_with(tmp_path, output)

    def test_failure_unlinks_tmp(self):
        source = mock.Mock()
        source.exists.return_value = True
        output = Path("out.mp4")
        tmp_path = Path("out.mp4.tmp")

        result = mock.Mock()
        result.returncode = 1
        result.stderr = "boom"
        with mock.patch("clip_pilot.ffmpeg_tools.subprocess.run", return_value=result):
            ok, error = cut_and_format(source, output, start=0.0, end=5.0, ffmpeg_path="ffmpeg")
        self.assertFalse(ok)
        self.assertEqual(error, "boom")
        self.assertFalse(tmp_path.exists())


if __name__ == "__main__":
    unittest.main()
