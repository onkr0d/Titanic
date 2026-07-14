"""Size-targeted ("shareable") encode: budget math and the two-pass x265 encode."""

import logging
import os
import subprocess

import ffmpeg

from fileutils import remove_quietly

logger = logging.getLogger(__name__)

# force pool for multicore util
_X265_PARAMS = f"pools={os.cpu_count() or 1}"

# Served via /api/config so the frontend's quality prediction can't drift.
SHAREABLE_AUDIO_KBPS = 128  # fixed AAC rate keeps the byte budget predictable
SHAREABLE_SIZE_MARGIN = 0.95  # headroom for container/muxing overhead
SHAREABLE_MIN_VIDEO_KBPS = 60  # floor so absurd targets still encode
SHAREABLE_MAX_TARGET_MB = 2000


def duration_from_probe(probe: dict) -> float | None:
    try:
        duration = float(probe["format"]["duration"])
        return duration if duration > 0 else None
    except (KeyError, ValueError):
        return None


def video_kbps_from_probe(probe: dict) -> int | None:
    """Video stream kbps; falls back to overall bitrate (MKV often omits it)."""
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            try:
                return int(stream["bit_rate"]) // 1000
            except (KeyError, ValueError):
                break
    try:
        return int(probe["format"]["bit_rate"]) // 1000
    except (KeyError, ValueError):
        return None


def audio_count_from_probe(probe: dict) -> int:
    return sum(1 for s in probe.get("streams", []) if s.get("codec_type") == "audio")


def fits_target(path: str, target_size_mb: float) -> bool:
    """No margin: an existing file's byte size is truth; margin only pads encodes."""
    return os.path.getsize(path) <= target_size_mb * 1024 * 1024


def shareable_video_kbps(
    target_size_mb: float,
    duration_sec: float,
    source_video_kbps: int | None = None,
    audio_streams: int = 1,
) -> int:
    """Capped at the source's own bitrate so an encode never inflates the file."""
    total_kbps = (target_size_mb * 8 * 1024) / duration_sec
    video_kbps = int(total_kbps * SHAREABLE_SIZE_MARGIN) - SHAREABLE_AUDIO_KBPS * max(
        audio_streams, 1
    )
    if source_video_kbps is not None:
        video_kbps = min(video_kbps, source_video_kbps)
    if video_kbps < SHAREABLE_MIN_VIDEO_KBPS:
        logger.warning(
            "Target %sMB over %.0fs leaves only %dkbps for video; flooring to %dkbps "
            "(result may exceed target and look rough).",
            target_size_mb, duration_sec, video_kbps, SHAREABLE_MIN_VIDEO_KBPS,
        )
        video_kbps = SHAREABLE_MIN_VIDEO_KBPS
    return video_kbps


def build_shareable_copy(
    source_file: str,
    target_size_mb: float,
    dest_dir: str,
    output_basename: str | None = None,
    *,
    dest_file: str | None = None,
    preserve_streams: bool = False,
) -> str:
    """Two-pass HEVC encode sized to land under target_size_mb, written as
    "<stem>_<N>MB.<ext>" (or exactly dest_file). preserve_streams keeps all
    audio/sub tracks — required when this is the sole delivered artifact."""
    try:
        probe = ffmpeg.probe(source_file)
    except ffmpeg.Error as e:
        raise ValueError(f"Cannot size-target {source_file}: probe failed") from e
    duration = duration_from_probe(probe)
    if not duration:
        raise ValueError(f"Cannot size-target {source_file}: unknown duration")

    stem, ext = os.path.splitext(output_basename or os.path.basename(source_file))
    if dest_file is None:
        # Normalize any int-valued target so filenames read "10MB" not "10.0MB".
        label = int(target_size_mb) if float(target_size_mb).is_integer() else target_size_mb
        dest_file = os.path.join(dest_dir, f"{stem}_{label}MB{ext or '.mp4'}")

    audio_streams = audio_count_from_probe(probe) if preserve_streams else 1
    video_kbps = shareable_video_kbps(
        target_size_mb, duration, video_kbps_from_probe(probe), audio_streams
    )

    # Per-encode stats file so concurrent workers don't clobber each other's pass log.
    stats_file = os.path.join(dest_dir, f".{stem}_{os.getpid()}_x265.log")
    common = [
        "ffmpeg", "-y", "-i", source_file,
        "-c:v", "libx265", "-b:v", f"{video_kbps}k", "-preset", "medium",
    ]
    try:
        subprocess.run(
            common
            + [
                "-x265-params", f"pass=1:stats={stats_file}:{_X265_PARAMS}",
                "-an", "-f", "null", os.devnull,
            ],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(
            common
            + (["-map", "0", "-c:s", "copy"] if preserve_streams else [])
            + [
                "-x265-params", f"pass=2:stats={stats_file}:{_X265_PARAMS}",
                "-tag:v", "hvc1",
                "-c:a", "aac", "-b:a", f"{SHAREABLE_AUDIO_KBPS}k",
                "-movflags", "+faststart", "-map_metadata", "0", dest_file,
            ],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error("Shareable encode failed: %s", e)
        logger.error("FFmpeg stderr: %s", e.stderr)
        # Drop the partial pass-2 output; nobody else knows this path exists yet.
        remove_quietly(dest_file)
        raise
    finally:
        # x265 writes "<stats>" and "<stats>.cutree"/".mbtree"; sweep them all.
        for suffix in ("", ".cutree", ".mbtree", ".temp"):
            remove_quietly(stats_file + suffix)

    logger.info("Built shareable copy (~%sMB target): %s", target_size_mb, dest_file)
    return dest_file
