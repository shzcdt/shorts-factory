"""Probing video files with ffprobe.

Pure functions: no database access, reusable for any file (e.g. URL mode).
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("clip_pilot.probe")


def parse_fps(r_frame_rate: str | None) -> float | None:
    """Parse an ffprobe frame rate string into a float.

    Args:
        r_frame_rate: Frame rate as reported by ffprobe (e.g. "30000/1001").

    Returns:
        Frames per second, or None if it cannot be parsed.
    """
    if not r_frame_rate:
        return None
    if "/" in r_frame_rate:
        num, den = r_frame_rate.split("/", 1)
        try:
            den_value = float(den)
            return float(num) / den_value if den_value != 0 else None
        except ValueError:
            return None
    try:
        return float(r_frame_rate)
    except ValueError:
        return None


def parse_resolution(width: int | None, height: int | None) -> str | None:
    """Format width and height into a resolution string.

    Args:
        width: Video stream width in pixels.
        height: Video stream height in pixels.

    Returns:
        Resolution string like "1920x1080", or None if either is missing.
    """
    if width and height:
        return f"{width}x{height}"
    return None


def probe_file(path: Path, ffprobe_path: str = "ffprobe") -> dict | None:
    """Extract normalized metadata from a video file.

    Args:
        path: Video file to probe.
        ffprobe_path: Path to the ffprobe binary.

    Returns:
        Dict with duration_seconds, resolution, fps and raw metadata_json,
        or None if probing failed.
    """
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        logger.error("Cannot run ffprobe (%s): %s", ffprobe_path, exc)
        return None
    if result.returncode != 0:
        logger.error("ffprobe failed for %s: %s", path, result.stderr.strip())
        return None
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("Cannot parse ffprobe output for %s: %s", path, exc)
        return None

    video_stream = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"), None)
    duration_raw = raw.get("format", {}).get("duration")
    try:
        duration = float(duration_raw) if duration_raw else None
    except (TypeError, ValueError):
        duration = None

    return {
        "duration_seconds": duration,
        "resolution": parse_resolution(
            video_stream.get("width") if video_stream else None,
            video_stream.get("height") if video_stream else None,
        ),
        "fps": parse_fps(video_stream.get("r_frame_rate") if video_stream else None),
        "metadata_json": raw,
    }
