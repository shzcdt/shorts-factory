"""Command-line entry point for the clip-pilot package."""

import argparse
import logging
import sys

from clip_pilot import db
from clip_pilot.config import Config
from clip_pilot.logging_setup import setup_logging


def main() -> None:
    """Parse CLI arguments, bootstrap config, logging and database."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="clip-pilot")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--version", action="store_true", help="Show version")
    args = parser.parse_args()

    if args.version:
        from clip_pilot import __version__

        print(f"clip-pilot {__version__}")
        return

    config = Config.load(args.config)
    setup_logging(config.logging.get("level", "INFO"), config.get_path("logs"))
    config.ensure_dirs()
    db.init_db(config.get_path("db"))
    logging.getLogger("clip_pilot").info("ClipPilot initialized. config=%s", args.config)
