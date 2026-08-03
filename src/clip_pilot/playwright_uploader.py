"""Playwright-based YouTube uploader using saved browser sessions.

Sessions are created manually via ``auth login`` (a headed browser where the
user signs in) and stored as Playwright ``storage_state`` JSON under the auth
directory. Uploads reuse that session, headless by default.

The upload page selectors are the fragile part of this module: YouTube ships
UI changes frequently, so they live at the top as constants. On failure a
screenshot and the page HTML are dumped into the logs directory for debugging.
"""

import logging
import re
import time
from pathlib import Path
from typing import Any

from clip_pilot.config import Config

logger = logging.getLogger("clip_pilot.playwright_uploader")

UPLOAD_URL = "https://www.youtube.com/upload"

# Fragile, locale-sensitive selectors for the YouTube upload studio.
SELECTOR_FILE_INPUT = "input[type='file']"
SELECTOR_TITLE = "div[aria-label='Add a title']"
SELECTOR_NOT_KIDS = "paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']"
SELECTOR_DONE_BUTTON = "ytcp-button#done-button"
SELECTOR_CONFIRM_BUTTON = "ytcp-button#confirm-button"
SELECTOR_VIDEO_LINK = "a[href*='/watch?v=']"


class VerificationRequired(RuntimeError):
    """Raised when YouTube asks the user to prove they are human."""


def _sync_playwright() -> Any:
    """Import and return a sync Playwright context manager (deferred import)."""
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def _human_typing(page: Any, selector: str, text: str) -> None:
    """Click a field and type slowly, like a human would."""
    page.click(selector)
    page.keyboard.press("Control+a")
    page.keyboard.type(text, delay=60)


def _dump_debug(config: Config, page: Any) -> None:
    """Save a screenshot and the page HTML for post-mortem debugging."""
    logs_dir = config.get_path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=str(logs_dir / f"upload_error_{stamp}.png"))
    except Exception as exc:  # noqa: BLE001 - debugging helper must not crash
        logger.warning("Screenshot failed: %s", exc)
    try:
        (logs_dir / f"upload_error_{stamp}.html").write_text(
            page.content(), encoding="utf-8", errors="replace"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("HTML dump failed: %s", exc)
    logger.error("Debug artifacts written to %s", logs_dir)


def _launch_kwargs(config: Config, *, headed: bool) -> dict:
    """Build chromium.launch kwargs: real Chrome channel + anti-automation flags."""
    pw = config.playwright
    kwargs: dict = {
        "headless": False if headed else bool(pw.get("headless", False)),
        "slow_mo": int(pw.get("slow_mo_ms", 0)),
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    channel = pw.get("channel")
    if channel:
        kwargs["channel"] = channel
    return kwargs


def _context_kwargs(config: Config, *, storage_state: str | None = None) -> dict:
    """Build browser.new_context kwargs, applying the configured user agent."""
    kwargs: dict = {"viewport": {"width": 1280, "height": 800}}
    if storage_state is not None:
        kwargs["storage_state"] = storage_state
    user_agent = config.playwright.get("user_agent")
    if user_agent:
        kwargs["user_agent"] = user_agent
    return kwargs


def save_session(config: Config, account_name: str) -> None:
    """Open a headed browser so the user can sign in, then store the session.

    Args:
        config: Application configuration (locates the auth directory).
        account_name: Name of the account; the session is stored as
            ``auth/<account_name>.json``.
    """
    session_path = config.get_path("auth") / f"{account_name}.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)

    with _sync_playwright() as p:
        browser = p.chromium.launch(**(_launch_kwargs(config, headed=True)))
        context = browser.new_context(**_context_kwargs(config))
        page = context.new_page()
        page.goto("https://www.youtube.com")
        logger.info("Sign in to YouTube in the opened window, then press Enter here")
        input("Press Enter after you are signed in...")
        context.storage_state(path=str(session_path))
        logger.info("Session saved to %s", session_path)
        context.close()
        browser.close()


class PlaywrightUploader:
    """Upload clips through the YouTube upload studio."""

    def __init__(self, config: Config, *, account_name: str) -> None:
        self.config = config
        self.account_name = account_name
        self.session_path = config.get_path("auth") / f"{account_name}.json"

    def upload(self, *, video_path: Path, title: str) -> str:
        """Upload a single video and return its YouTube video id.

        Args:
            video_path: Local path of the clip to upload.
            title: Video title to set on YouTube.

        Returns:
            The resulting YouTube video id.

        Raises:
            RuntimeError: If no saved session exists, or the upload could not
                be completed (selector drift, login lost, etc.).
            VerificationRequired: If YouTube asks for a human verification.
        """
        if not self.session_path.exists():
            raise RuntimeError(
                f"Session {self.session_path} not found. "
                f"Run 'auth login --name {self.account_name}' first."
            )

        cfg = self.config.playwright
        timeout_ms = int(cfg.get("timeout_seconds", 300)) * 1000

        with _sync_playwright() as p:
            browser = p.chromium.launch(**_launch_kwargs(self.config, headed=False))
            context = browser.new_context(
                **_context_kwargs(self.config, storage_state=str(self.session_path))
            )
            page = context.new_page()
            try:
                return self._do_upload(page, video_path, title, timeout_ms)
            except VerificationRequired:
                raise
            except Exception as exc:
                logger.error("Upload failed, dumping debug info")
                _dump_debug(self.config, page)
                raise RuntimeError(str(exc)) from exc
            finally:
                context.close()
                browser.close()

    def _do_upload(self, page: Any, video_path: Path, title: str, timeout_ms: int) -> str:
        logger.info("Opening upload page")
        page.goto(UPLOAD_URL, timeout=60000)

        file_input = page.locator(SELECTOR_FILE_INPUT)
        if file_input.count() == 0:
            raise RuntimeError("Upload page not loaded (not signed in?)")
        file_input.set_input_files(str(video_path))
        logger.info("File selected, waiting for the studio to load")

        title_field = page.locator(SELECTOR_TITLE)
        title_field.wait_for(state="visible", timeout=timeout_ms)
        _human_typing(page, SELECTOR_TITLE, title)
        logger.info("Title entered: %s", title)

        not_kids = page.locator(SELECTOR_NOT_KIDS)
        if not_kids.count() > 0:
            not_kids.click()

        done = page.locator(SELECTOR_DONE_BUTTON)
        done.wait_for(state="visible", timeout=timeout_ms)
        done.click()
        confirm = page.locator(SELECTOR_CONFIRM_BUTTON)
        confirm.wait_for(state="visible", timeout=60000)
        confirm.click()
        logger.info("Publishing")

        video_id = self._extract_video_id(page, timeout_ms)
        logger.info("Published, video id: %s", video_id)
        return video_id

    def _extract_video_id(self, page: Any, timeout_ms: int) -> str:
        """Wait for the published video to appear and parse its id."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            links = page.locator(SELECTOR_VIDEO_LINK)
            if links.count() > 0:
                href = links.first.get_attribute("href")
                match = re.search(r"[?&]v=([\w-]{11})", href or "")
                if match:
                    return match.group(1)
            page.wait_for_timeout(2000)
        raise RuntimeError("Could not find the published video id")
