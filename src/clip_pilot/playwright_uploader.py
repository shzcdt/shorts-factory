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
import sys
import time
from pathlib import Path
from typing import Any

from clip_pilot.config import Config

logger = logging.getLogger("clip_pilot.playwright_uploader")

UPLOAD_URL = "https://www.youtube.com/upload"
STUDIO_URL_PREFIX = "https://studio.youtube.com"

# Locale-independent selectors for the YouTube upload studio. Everything is
# matched by element id/name attributes, never by visible text, so the flow
# survives any studio interface language.
SELECTOR_FILE_INPUT = "input[type='file']"
# The title field is a contenteditable div; aria-labels here are localized.
SELECTOR_TITLE_CANDIDATES = (
    "ytcp-social-suggestions-textbox#title-textarea #textbox",
    "ytcp-uploads-dialog #textbox",
)
SELECTOR_NOT_KIDS = "[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']"
SELECTOR_DONE_BUTTON = "ytcp-button#done-button"
SELECTOR_CONFIRM_BUTTON = "ytcp-button#confirm-button"
SELECTOR_VIDEO_LINK = "a[href*='/watch?v=']"
SELECTOR_NEXT_BUTTON = "ytcp-button#next-button"
SELECTOR_PUBLIC_RADIO = "[name='PUBLIC']"


class VerificationRequired(RuntimeError):
    """Raised when YouTube asks the user to prove they are human."""


class LoginLost(RuntimeError):
    """Raised when the saved session no longer signs the user in."""


def _sync_playwright() -> Any:
    """Import and return a sync Playwright context manager (deferred import)."""
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def _human_typing(page: Any, selector: str, text: str) -> None:
    """Click a field, select all existing text and type slowly.

    Select-all uses Cmd on macOS and Ctrl elsewhere (YouTube's select-all
    shortcut follows the platform convention).
    """
    page.click(selector)
    select_all = "Meta+a" if sys.platform == "darwin" else "Control+a"
    page.keyboard.press(select_all)
    page.keyboard.press("Delete")
    page.keyboard.type(text, delay=60)


def _is_logged_out(url: str) -> bool:
    """Return True if the browser was redirected to a Google sign-in page."""
    return "accounts.google.com" in url or "ServiceLogin" in url


def _fill_title(page: Any, title: str, timeout_ms: int) -> None:
    """Type the title into the first title selector present in the dialog."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for selector in SELECTOR_TITLE_CANDIDATES:
            field = page.locator(selector)
            if field.count() > 0:
                field.first.wait_for(state="visible", timeout=10000)
                _human_typing(page, selector, title)
                logger.info("Title entered via %s: %s", selector, title)
                return
        page.wait_for_timeout(1000)
    raise RuntimeError(
        "Title field not found; upload studio layout may have changed "
        "(selectors: " + ", ".join(SELECTOR_TITLE_CANDIDATES) + ")"
    )


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
    """Build browser.new_context kwargs.

    The locale is pinned to en-US so YouTube renders the studio in English;
    all upload selectors are locale-independent anyway, but English keeps the
    debug HTML dumps readable.
    """
    kwargs: dict = {
        "viewport": {"width": 1280, "height": 800},
        "locale": config.playwright.get("locale", "en-US"),
    }
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


def session_valid(config: Config, account_name: str) -> bool:
    """Check whether a saved session still signs the user into YouTube.

    Opens youtube.com with the stored cookies (never headless-undetectable
    tricks needed for this) and returns False if YouTube redirects to the
    Google sign-in page or the account avatar is missing.
    """
    session_path = config.get_path("auth") / f"{account_name}.json"
    if not session_path.exists():
        return False

    with _sync_playwright() as p:
        browser = p.chromium.launch(**_launch_kwargs(config, headed=False))
        context = browser.new_context(**_context_kwargs(config, storage_state=str(session_path)))
        page = context.new_page()
        try:
            page.goto("https://www.youtube.com/account", timeout=60000)
            if _is_logged_out(page.url):
                return False
            avatar = page.locator("button#avatar-btn, img#avatar-img")
            return bool(avatar.count() > 0)
        finally:
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
        if _is_logged_out(page.url):
            raise LoginLost(
                "Saved session is not signed in. "
                f"Run 'auth login --name {self.account_name}' again. URL: {page.url}"
            )

        # The file input appears only after the studio iframe finishes loading.
        file_input = page.locator(SELECTOR_FILE_INPUT)
        try:
            file_input.first.wait_for(state="attached", timeout=30000)
        except Exception as exc:
            if _is_logged_out(page.url):
                raise LoginLost(
                    f"Session expired. Run 'auth login --name {self.account_name}' again."
                ) from exc
            raise RuntimeError("Upload page did not load (no file input found)") from exc
        file_input.first.set_input_files(str(video_path))
        logger.info("File selected, waiting for the studio to load")

        _fill_title(page, title, timeout_ms)

        self._finish_wizard(page, timeout_ms)
        logger.info("Save clicked, confirming")

        confirm = page.locator(SELECTOR_CONFIRM_BUTTON)
        try:
            confirm.first.wait_for(state="visible", timeout=30000)
            confirm.first.click()
        except Exception:
            # Some studio versions publish directly after Save.
            logger.info("No confirm dialog, assuming direct publish")
        logger.info("Publishing")

        video_id = self._extract_video_id(page, timeout_ms)
        logger.info("Published, video id: %s", video_id)
        return video_id

    def _finish_wizard(self, page: Any, timeout_ms: int) -> None:
        """Walk the multi-step upload wizard until Save becomes clickable.

        The studio shows a sequence of steps (Details → Video elements →
        Checks → Visibility), each with a Next button; the last step has the
        Save button. While a video is still uploading/processing Next stays
        disabled, so this loop just keeps clicking whatever is available.

        The mandatory "made for kids" radio is (re-)clicked on every pass:
        it may appear later than the first pass, and clicking an already
        selected radio is harmless.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            not_kids = page.locator(SELECTOR_NOT_KIDS)
            if not_kids.count() > 0 and not_kids.first.is_visible():
                not_kids.first.click()

            public = page.locator(SELECTOR_PUBLIC_RADIO)
            if public.count() > 0 and public.first.is_visible():
                public.first.click()

            done = page.locator(f"{SELECTOR_DONE_BUTTON}:not([disabled])")
            if done.count() > 0 and done.first.is_visible():
                done.first.click()
                return

            nxt = page.locator(f"{SELECTOR_NEXT_BUTTON}:not([disabled])")
            if nxt.count() > 0 and nxt.first.is_visible():
                nxt.first.click()
                logger.info("Wizard: Next clicked")
            page.wait_for_timeout(2000)
        raise RuntimeError("Upload wizard did not reach the Save step in time")

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
