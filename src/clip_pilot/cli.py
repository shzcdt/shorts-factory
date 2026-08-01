"""Command-line entry point for the clip-pilot package."""

import argparse
import logging
import sqlite3
import sys

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
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="clip-pilot")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--version", action="store_true", help="Show version")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("watch", help="Watch the inbox folder and ingest new videos")
    subparsers.add_parser("scan", help="Scan the inbox folder once and ingest new videos")
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
    else:
        logger.info("ClipPilot initialized. config=%s", args.config)
