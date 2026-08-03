"""Command-line entry point for the clip-pilot package."""

import argparse
import io
import logging
import sqlite3
import sys
from typing import cast

from clip_pilot import db
from clip_pilot.config import Config
from clip_pilot.logging_setup import setup_logging

logger = logging.getLogger("clip_pilot")


def _bootstrap(config_path: str) -> tuple[Config, sqlite3.Connection]:
    """Load config, set up logging/dirs and initialize the database."""
    config = Config.load(config_path)
    setup_logging(config.logging.get("level", "INFO"), config.get_path("logs"))
    config.ensure_dirs()
    conn = db.init_db(config.get_path("db"))
    return config, conn


def main() -> None:
    """Parse CLI arguments, bootstrap and run the requested command."""
    for stream in (sys.stdout, sys.stderr):
        try:
            wrapper = cast(io.TextIOWrapper, stream)
            wrapper.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(prog="clip-pilot")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--version", action="store_true", help="Show version")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("watch", help="Watch the inbox folder and ingest new videos")
    subparsers.add_parser("scan", help="Scan the inbox folder once and ingest new videos")
    subparsers.add_parser("analyze", help="Analyze new sources with ffprobe")
    segment_parser = subparsers.add_parser(
        "segment", help="Segment analyzed sources into clip candidates"
    )
    segment_parser.add_argument(
        "--min-seconds",
        type=int,
        default=None,
        help="Minimum clip length in seconds (overrides config)",
    )
    segment_parser.add_argument(
        "--max-seconds",
        type=int,
        default=None,
        help="Maximum clip length in seconds (overrides config)",
    )
    format_parser = subparsers.add_parser("format", help="Format cut clips with ffmpeg")
    format_parser.add_argument(
        "--limit", type=int, default=None, help="Maximum number of clips to format"
    )
    format_parser.add_argument(
        "--source-id", type=int, default=None, help="Only format clips of this source"
    )
    retry_parser = subparsers.add_parser("retry", help="Reset a source for re-analysis")
    retry_parser.add_argument("source_id", type=int, help="Source id to reset")
    reset_parser = subparsers.add_parser(
        "reset", help="Delete clips of a segmented source and return it to done"
    )
    reset_parser.add_argument(
        "--source-id", type=int, required=True, help="Source id whose clips to delete"
    )
    review_parser = subparsers.add_parser(
        "review", help="Review workflow: prepare, list, approve, reject"
    )
    review_sub = review_parser.add_subparsers(dest="review_command")
    prepare_parser = review_sub.add_parser(
        "prepare", help="Move ready clips into the review folder"
    )
    prepare_parser.add_argument(
        "--limit", type=int, default=None, help="Maximum number of clips to move"
    )
    prepare_parser.add_argument(
        "--source-id", type=int, default=None, help="Only move clips of this source"
    )
    review_sub.add_parser("list", help="List clips pending review")
    approve_parser = review_sub.add_parser("approve", help="Approve a clip pending review")
    approve_parser.add_argument(
        "--all", action="store_true", help="Approve all clips pending review"
    )
    approve_parser.add_argument(
        "clip_id", type=int, nargs="?", default=None, help="Clip id to approve"
    )
    reject_parser = review_sub.add_parser("reject", help="Reject a clip pending review")
    reject_parser.add_argument("--all", action="store_true", help="Reject all clips pending review")
    reject_parser.add_argument("--reason", type=str, default=None, help="Optional rejection reason")
    reject_parser.add_argument(
        "clip_id", type=int, nargs="?", default=None, help="Clip id to reject"
    )
    auth_parser = subparsers.add_parser(
        "auth", help="Manage YouTube accounts (saved browser sessions)"
    )
    auth_sub = auth_parser.add_subparsers(dest="auth_command")
    login_parser = auth_sub.add_parser(
        "login", help="Sign in to YouTube in a browser and save the session"
    )
    login_parser.add_argument("--name", type=str, required=True, help="Account name")
    auth_sub.add_parser("status", help="List accounts and session validity")
    logout_parser = auth_sub.add_parser("logout", help="Delete a saved session")
    logout_parser.add_argument("--name", type=str, required=True, help="Account name")
    publish_parser = subparsers.add_parser("publish", help="Publish approved clips to YouTube")
    publish_parser.add_argument("--account", type=str, required=True, help="Account name")
    publish_parser.add_argument("--limit", type=int, default=None, help="Max clips to publish")
    args = parser.parse_args()

    if args.version:
        from clip_pilot import __version__

        print(f"clip-pilot {__version__}")
        return

    config, conn = _bootstrap(args.config)

    if args.command == "watch":
        from clip_pilot.watcher import watch_inbox

        logger.info("Watching inbox. Ctrl+C to stop.")
        watch_inbox(conn, config)
    elif args.command == "scan":
        from clip_pilot.watcher import scan_inbox

        count = scan_inbox(conn, config)
        logger.info("Ingested %s new source(s)", count)
    elif args.command == "analyze":
        from clip_pilot.analyzer import analyze_new_sources

        count = analyze_new_sources(conn, config)
        logger.info("Analyzed %s source(s)", count)
    elif args.command == "segment":
        from clip_pilot.segmenter import segment_done_sources

        count = segment_done_sources(
            conn, config, min_seconds=args.min_seconds, max_seconds=args.max_seconds
        )
        logger.info("Segmented %s source(s)", count)
    elif args.command == "format":
        from clip_pilot.formatter import format_cut_clips

        count = format_cut_clips(conn, config, limit=args.limit, source_id=args.source_id)
        logger.info("Formatted %s clip(s)", count)
    elif args.command == "retry":
        from clip_pilot.analyzer import retry_source

        if retry_source(conn, args.source_id):
            logger.info("Source %s reset. Run 'analyze' to reprocess it.", args.source_id)
        else:
            logger.warning("Source %s could not be reset", args.source_id)
    elif args.command == "reset":
        from clip_pilot.segmenter import reset_source

        try:
            count = reset_source(conn, args.source_id)
        except ValueError as exc:
            logger.warning("Reset failed: %s", exc)
        else:
            logger.info("Source %s reset to done, deleted %s clip(s)", args.source_id, count)
    elif args.command == "review":
        from clip_pilot.review import (
            approve_all,
            approve_clip,
            list_clips_for_review,
            prepare_for_review,
            reject_all,
            reject_clip,
        )

        if args.review_command == "prepare":
            count = prepare_for_review(conn, config, limit=args.limit, source_id=args.source_id)
            logger.info("Moved %s clip(s) to review", count)
        elif args.review_command == "list":
            clips = list_clips_for_review(conn)
            if not clips:
                logger.info("No clips pending review")
            for clip in clips:
                print(
                    f"id={clip['id']} source={clip['source_id']} "
                    f"{clip['start_time']:.0f}-{clip['end_time']:.0f}s {clip['path']}"
                )
        elif args.review_command in ("approve", "reject"):
            if args.all:
                count = (
                    approve_all(conn)
                    if args.review_command == "approve"
                    else reject_all(conn, config, reason=args.reason)
                )
                logger.info("%s %s clip(s)", args.review_command + "d", count)
            elif args.clip_id is None:
                logger.warning(
                    "Specify a clip id or --all: e.g. 'review %s 3' or 'review %s --all'",
                    args.review_command,
                    args.review_command,
                )
            else:
                try:
                    if args.review_command == "approve":
                        status = approve_clip(conn, args.clip_id)
                    else:
                        status = reject_clip(conn, config, args.clip_id, reason=args.reason)
                except ValueError as exc:
                    logger.warning("%s", exc)
                else:
                    if status is not None:
                        logger.info("Clip %s %s", args.clip_id, args.review_command + "d")
                    else:
                        logger.warning("Clip %s is not pending review", args.clip_id)
        else:
            logger.info("Specify a review subcommand: prepare | list | approve | reject")
    elif args.command == "auth":
        from clip_pilot import playwright_uploader, repo

        if args.auth_command == "login":
            if repo.get_account_by_name(conn, args.name) is None:
                repo.create_account(conn, name=args.name)
                conn.commit()
                logger.info("Account %r created", args.name)
            playwright_uploader.save_session(config, args.name)
        elif args.auth_command == "status":
            accounts = repo.get_accounts(conn)
            if not accounts:
                logger.info("No accounts yet. Run 'auth login --name <name>'")
            auth_dir = config.get_path("auth")
            for acc in accounts:
                session = auth_dir / f"{acc['name']}.json"
                state = "ok" if session.exists() else "no session"
                print(
                    f"name={acc['name']} status={acc['status']} "
                    f"session={state} posts_today={acc['posts_today']}"
                )
        elif args.auth_command == "logout":
            session_path = config.get_path("auth") / f"{args.name}.json"
            if session_path.exists():
                session_path.unlink()
                logger.info("Session %s removed", args.name)
            else:
                logger.warning("No session found for %s", args.name)
        else:
            logger.info("Specify an auth subcommand: login | status | logout")
    elif args.command == "publish":
        from clip_pilot.uploader import publish_approved_clips

        try:
            results = publish_approved_clips(
                conn, config, account_name=args.account, limit=args.limit
            )
        except ValueError as exc:
            logger.warning("%s", exc)
        else:
            logger.info(
                "Publish done: published=%s failed=%s", results["published"], results["failed"]
            )
            for err in results["errors"]:
                logger.error("  clip %s: %s", err["clip_id"], err["error"])
    else:
        logger.info("ClipPilot initialized. config=%s", args.config)
