"""ffmpeg helpers for cutting and formatting clips.

Pure functions: no database access, reusable for any file (e.g. URL mode).
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("clip_pilot.ffmpeg_tools")

STRATEGY_CENTER_CROP = "center_crop"
STRATEGY_BLUR_BACKGROUND = "blur_background"


def build_filter(
    start: float,
    end: float,
    *,
    width: int = 1080,
    height: int = 1920,
    strategy: str = STRATEGY_CENTER_CROP,
) -> str:
    """Build the ffmpeg filtergraph for a clip.

    Args:
        start: Clip start time in seconds.
        end: Clip end time in seconds.
        width: Target width in pixels.
        height: Target height in pixels.
        strategy: Formatting strategy ("center_crop" for MVP).

    Returns:
        An ffmpeg -vf filtergraph string.
    """
    if strategy == STRATEGY_BLUR_BACKGROUND:
        filtergraph = (
            f"scale=w={width}:h={height}:force_original_aspect_ratio=increase,"
            f"crop=w={width}:h={height},"
            f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
            f"pad=w={width}:h={height}:(ow-iw)/2:(oh-ih)/2,"
            f"boxblur=20:5,fps=30"
        )
    else:
        filtergraph = (
            f"scale=w={width}:h={height}:force_original_aspect_ratio=increase,"
            f"crop=w={width}:h={height},fps=30"
        )
    return filtergraph


def cut_and_format(
    source_path: Path,
    output_path: Path,
    *,
    start: float,
    end: float,
    ffmpeg_path: str = "ffmpeg",
    width: int = 1080,
    height: int = 1920,
    strategy: str = STRATEGY_CENTER_CROP,
) -> tuple[bool, str | None]:
    """Cut and reformat a clip from a source video.

    Args:
        source_path: Source video file.
        output_path: Destination for the formatted clip.
        start: Clip start time in seconds.
        end: Clip end time in seconds.
        ffmpeg_path: Path to the ffmpeg binary.
        width: Target width in pixels.
        height: Target height in pixels.
        strategy: Formatting strategy.

    Returns:
        (True, None) on success, (False, error) on failure.
    """
    if not source_path.exists():
        logger.error("Source file missing: %s", source_path)
        return False, f"source file missing: {source_path}"

    duration = max(end - start, 0.1)
    filtergraph = build_filter(start, end, width=width, height=height, strategy=strategy)
    tmp_path = output_path.with_name(output_path.name + ".tmp")

    cmd = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_path),
        "-vf",
        filtergraph,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(tmp_path),
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
        logger.error("Cannot run ffmpeg (%s): %s", ffmpeg_path, exc)
        tmp_path.unlink(missing_ok=True)
        return False, str(exc)

    if result.returncode != 0:
        logger.error("ffmpeg failed for %s: %s", source_path, result.stderr.strip())
        tmp_path.unlink(missing_ok=True)
        return False, result.stderr.strip() or "ffmpeg returned non-zero"

    os.replace(tmp_path, output_path)
    logger.info("Clip formatted: %s", output_path)
    return True, None
