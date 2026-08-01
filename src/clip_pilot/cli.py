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
    else:
        logger.info("ClipPilot initialized. config=%s", args.config)
