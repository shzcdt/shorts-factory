import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clip_pilot.config import Config
from clip_pilot.playwright_uploader import (
    PlaywrightUploader,
    save_session,
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


class TestPlaywrightUploader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = make_config(self.root)
        self.config.ensure_dirs()

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload_session_missing_raises(self):
        uploader = PlaywrightUploader(self.config, account_name="main")
        with self.assertRaises(RuntimeError):
            uploader.upload(video_path=Path("x.mp4"), title="t")

    def test_save_session_writes_storage_state(self):
        saved: dict = {}

        class FakeContext:
            def storage_state(self, *, path):
                saved["path"] = path

            def new_page(self):
                return mock.MagicMock()

            def close(self):
                pass

        class FakeBrowser:
            def new_context(self, **kwargs):
                return FakeContext()

            def close(self):
                pass

        class FakePlaywright:
            def __enter__(self):
                p = mock.MagicMock()
                p.chromium.launch.return_value = FakeBrowser()
                return p

            def __exit__(self, *args):
                pass

        with (
            mock.patch(
                "clip_pilot.playwright_uploader._sync_playwright",
                side_effect=FakePlaywright,
            ),
            mock.patch("builtins.input", return_value=""),
        ):
            save_session(self.config, "main")
        self.assertEqual(saved["path"], str(self.config.get_path("auth") / "main.json"))

    def test_upload_uses_session_and_returns_id(self):
        session_path = self.config.get_path("auth") / "main.json"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text("{}")
        seen: dict = {}

        class FakeContext:
            def new_page(self):
                return mock.MagicMock()

            def close(self):
                pass

        class FakeBrowser:
            def new_context(self, **kwargs):
                seen["storage_state"] = kwargs.get("storage_state")
                return FakeContext()

            def close(self):
                pass

        class FakePlaywright:
            def __enter__(self):
                p = mock.MagicMock()
                p.chromium.launch.return_value = FakeBrowser()
                return p

            def __exit__(self, *args):
                pass

        with (
            mock.patch(
                "clip_pilot.playwright_uploader._sync_playwright",
                side_effect=FakePlaywright,
            ),
            mock.patch.object(PlaywrightUploader, "_do_upload", return_value="vid123"),
        ):
            uploader = PlaywrightUploader(self.config, account_name="main")
            video_id = uploader.upload(video_path=Path("x.mp4"), title="t")
        self.assertEqual(video_id, "vid123")
        self.assertEqual(seen["storage_state"], str(session_path))

    def test_extract_video_id(self):
        page = mock.MagicMock()
        link = mock.MagicMock()
        link.get_attribute.return_value = "https://www.youtube.com/watch?v=abcXYZ12345"
        locator = mock.MagicMock()
        locator.count.return_value = 1
        locator.first = link
        page.locator.return_value = locator
        uploader = PlaywrightUploader(self.config, account_name="main")
        self.assertEqual(uploader._extract_video_id(page, 5000), "abcXYZ12345")

    def test_do_upload_runs_through_publication(self):
        page = mock.MagicMock()
        file_input = mock.MagicMock()
        file_input.count.return_value = 1
        page.locator.side_effect = lambda sel: file_input
        uploader = PlaywrightUploader(self.config, account_name="main")
        with mock.patch.object(uploader, "_extract_video_id", return_value="vid99") as extract:
            video_id = uploader._do_upload(page, Path("clip.mp4"), "Title", timeout_ms=5000)
        self.assertEqual(video_id, "vid99")
        file_input.set_input_files.assert_called_once_with("clip.mp4")
        extract.assert_called_once()
        self.assertEqual(page.goto.call_args[0][0], "https://www.youtube.com/upload")


if __name__ == "__main__":
    unittest.main()
