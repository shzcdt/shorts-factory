"""Scene detection on video files with PySceneDetect.

Pure functions: no database access, reusable for any file (e.g. URL mode).
"""

import logging
from pathlib import Path

from scenedetect import detect
from scenedetect.detectors import AdaptiveDetector, ContentDetector

logger = logging.getLogger("clip_pilot.scenes")

DETECTOR_CONTENT = "content"
DETECTOR_ADAPTIVE = "adaptive"


def detect_scenes(
    path: Path | str, *, detector: str = DETECTOR_CONTENT, threshold: float = 27.0
) -> list[tuple[float, float]]:
    """Detect scene boundaries in a video file.

    Args:
        path: Video file to analyze.
        detector: Detector name: "content" or "adaptive".
        threshold: Detector sensitivity (lower = more scene cuts).

    Returns:
        List of (start, end) scene boundaries in seconds, or [] on failure.
    """
    try:
        if detector == DETECTOR_ADAPTIVE:
            scene_detector = AdaptiveDetector(threshold=threshold)
        else:
            scene_detector = ContentDetector(threshold=threshold)
        scene_list = detect(str(path), scene_detector)
    except Exception:
        logger.exception("Scene detection failed for %s", path)
        return []
    scenes = [
        (float(scene[0].get_seconds()), float(scene[1].get_seconds())) for scene in scene_list
    ]
    logger.info("Detected %d scene(s) in %s", len(scenes), path)
    return scenes


def merge_and_filter_scenes(
    scenes: list[tuple[float, float]],
    *,
    min_scene_seconds: float = 3.0,
    min_clip_seconds: float = 15.0,
    max_clip_seconds: float = 60.0,
) -> list[tuple[float, float]]:
    """Merge scenes into clips of a target length range.

    Very short scenes are absorbed into neighbors, then scenes are greedily
    accumulated into clips of at least min_clip_seconds, and finally over-long
    clips are split into max_clip_seconds chunks.

    Args:
        scenes: List of (start, end) scene boundaries in seconds.
        min_scene_seconds: Scenes shorter than this are absorbed into a neighbor.
        min_clip_seconds: Target minimum clip length in seconds.
        max_clip_seconds: Clips longer than this are split into equal chunks.

    Returns:
        List of (start, end) segments in seconds.
    """
    if not scenes:
        return []

    merged: list[list[float]] = []
    for start, end in scenes:
        if not merged:
            merged.append([start, end])
            continue
        if end - start < min_scene_seconds:
            merged[-1][1] = end
        else:
            merged.append([start, end])

    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_scene_seconds:
        merged[1][0] = merged[0][0]
        del merged[0]

    accumulated: list[tuple[float, float]] = []
    for start, end in merged:
        if not accumulated:
            accumulated.append((start, end))
            continue
        last_start, last_end = accumulated[-1]
        if last_end - last_start < min_clip_seconds:
            accumulated[-1] = (last_start, end)
        else:
            accumulated.append((start, end))

    if len(accumulated) > 1 and accumulated[-1][1] - accumulated[-1][0] < min_clip_seconds:
        prev_start, _ = accumulated[-2]
        accumulated[-2] = (prev_start, accumulated[-1][1])
        del accumulated[-1]

    result: list[tuple[float, float]] = []
    for start, end in accumulated:
        duration = end - start
        if duration > max_clip_seconds:
            chunks = int(duration / max_clip_seconds) + (1 if duration % max_clip_seconds else 0)
            chunk_size = duration / chunks
            for i in range(chunks):
                result.append(
                    (round(start + i * chunk_size, 3), round(start + (i + 1) * chunk_size, 3))
                )
        else:
            result.append((start, end))
    return result
