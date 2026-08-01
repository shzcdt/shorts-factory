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
    subparsers.add_parser("segment", help="Segment analyzed sources into clip candidates")
    retry_parser = subparsers.add_parser("retry", help="Reset a source for re-analysis")
    retry_parser.add_argument("source_id", type=int, help="Source id to reset")
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

        count = segment_done_sources(conn, config)
        logger.info("Segmented %s source(s)", count)
    elif args.command == "retry":
        from clip_pilot.analyzer import retry_source

        if retry_source(conn, args.source_id):
            logger.info("Source %s reset. Run 'analyze' to reprocess it.", args.source_id)
        else:
            logger.warning("Source %s could not be reset", args.source_id)
    else:
        logger.info("ClipPilot initialized. config=%s", args.config)
