import functools
import json
import logging
import os
import random
import shutil
import subprocess
import time

import ffmpeg
import firebase_admin
import requests
import rq
from firebase_admin import credentials
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor

from fileutils import remove_quietly

# Logging configuration note:
# - When imported by app.py, this module's basicConfig runs first; app.py's later
#   basicConfig call is therefore a no-op.
# - When used by standalone workers, worker.py configures logging (via basicConfig)
#   before RQ imports this module, so this module's basicConfig is usually a no-op.
_is_dev = os.environ.get("IS_DEV", "false").lower() == "true"
logging.basicConfig(
    level=logging.DEBUG if _is_dev else logging.INFO,
    format="%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

# force pool for multicore util
_X265_PARAMS = f"pools={os.cpu_count() or 1}"

# Size-targeted "shareable" copies re-encode audio to a fixed AAC bitrate so the
# byte budget is predictable (the main pipeline stream-copies audio, which isn't).
_SHAREABLE_AUDIO_KBPS = 128
# Leave headroom under the requested size for container/muxing overhead so the
# result actually lands under a hard limit (e.g. Discord) rather than just near it.
_SHAREABLE_SIZE_MARGIN = 0.95
# Floor the video bitrate so an absurdly small target still produces a valid file
# instead of failing the encode; the UI already warns when quality will be rough.
_SHAREABLE_MIN_VIDEO_KBPS = 60


def initialize_firebase():
    """
    Initialize Firebase Admin SDK if not already initialized.
    This is needed because RQ workers run in separate processes.
    In dev/CI without credentials we warn and continue; in production we fail hard.
    """
    try:
        # Check if Firebase is already initialized
        firebase_admin.get_app()
        logger.debug("Firebase app already initialized")
    except ValueError:
        # Firebase not initialized, so initialize it
        logger.debug("Initializing Firebase app for job worker")

        # Get credentials path from environment or use default
        cred_path = os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "admin-sdk-cred.json",
            ),
        )

        if not os.path.exists(cred_path):
            if _is_dev:
                logger.warning(
                    "Firebase credentials not found at %s — running without Firebase (dev/CI mode)",
                    cred_path,
                )
                return
            raise FileNotFoundError(f"Firebase credentials file not found: {cred_path}")

        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        logger.debug("Firebase app initialized successfully")


def get_video_codec(input_file: str) -> str:
    """
    Get the video codec of a video file using ffprobe.
    Returns the codec name (e.g., 'h264', 'hevc', 'h265').
    """
    try:
        # Use ffprobe to get video stream information
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "v:0",  # Only video streams
            input_file,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        if data["streams"]:
            codec_name = data["streams"][0]["codec_name"].lower()
            logger.debug(f"Detected video codec: {codec_name}")
            return codec_name
        else:
            logger.warning(f"No video streams found in {input_file}")
            return None

    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe failed for {input_file}: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse ffprobe output: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting codec for {input_file}: {e}")
        return None


def get_video_duration(input_file: str) -> float:
    """
    Get a video's duration in seconds via ffprobe. Returns None if it can't be
    determined (needed to turn a target file size into a bitrate budget).
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            input_file,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0.0))
        return duration if duration > 0 else None
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError, KeyError) as e:
        logger.error(f"Failed to probe duration for {input_file}: {e}")
        return None


def build_shareable_copy(source_file: str, target_size_mb: float, dest_dir: str) -> str:
    """
    Two-pass HEVC encode of source_file sized to land under target_size_mb, written
    into dest_dir as "<stem>_<N>MB.<ext>". This is a *separate* shareable artifact
    (e.g. to drop in Discord); the full-quality copy is produced independently.

    Bitrate budget: total_kbps = target_bits / duration, minus the fixed AAC audio
    track, times a safety margin for container overhead so it clears a hard cap.
    """
    duration = get_video_duration(source_file)
    if not duration:
        raise ValueError(f"Cannot size-target {source_file}: unknown duration")

    stem, ext = os.path.splitext(os.path.basename(source_file))
    # Normalize any int-valued target so filenames read "10MB" not "10.0MB".
    label = int(target_size_mb) if float(target_size_mb).is_integer() else target_size_mb
    dest_file = os.path.join(dest_dir, f"{stem}_{label}MB{ext or '.mp4'}")

    total_kbps = (target_size_mb * 8 * 1024) / duration
    video_kbps = int(total_kbps * _SHAREABLE_SIZE_MARGIN) - _SHAREABLE_AUDIO_KBPS
    if video_kbps < _SHAREABLE_MIN_VIDEO_KBPS:
        logger.warning(
            "Target %sMB over %.0fs leaves only %dkbps for video; flooring to %dkbps "
            "(result may exceed target and look rough).",
            target_size_mb, duration, video_kbps, _SHAREABLE_MIN_VIDEO_KBPS,
        )
        video_kbps = _SHAREABLE_MIN_VIDEO_KBPS

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
            + [
                "-x265-params", f"pass=2:stats={stats_file}:{_X265_PARAMS}",
                "-tag:v", "hvc1",
                "-c:a", "aac", "-b:a", f"{_SHAREABLE_AUDIO_KBPS}k",
                "-movflags", "+faststart", "-map_metadata", "0", dest_file,
            ],
            capture_output=True, text=True, check=True,
        )
    finally:
        # x265 writes "<stats>" and "<stats>.cutree"/".mbtree"; sweep them all.
        for suffix in ("", ".cutree", ".mbtree", ".temp"):
            remove_quietly(stats_file + suffix)

    logger.info("Built shareable copy (~%sMB target): %s", target_size_mb, dest_file)
    return dest_file


def get_audio_streams(input_file: str) -> list:
    """
    Get all audio streams in a video file using ffprobe.
    Returns a list of audio stream information.
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "a",  # All audio streams
            input_file,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        audio_streams = data.get("streams", [])
        logger.debug(f"Detected {len(audio_streams)} audio stream(s)")
        return audio_streams

    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe failed for {input_file}: {e}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse ffprobe output: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error getting audio streams for {input_file}: {e}")
        return []


def process_audio_with_rnnoise(input_file: str, output_file: str) -> str:
    """
    Build a new DEFAULT mix from system (0:a:0) + mic (0:a:1 denoised),
    run EBU R128 (loudnorm) TWO-PASS on the mixed track,
    and keep both originals untouched. Video is copied.
    """
    try:
        audio_streams = get_audio_streams(input_file)
        if not audio_streams:
            logger.warning("No audio streams; skipping audio processing")
            return None

        # We expect exactly two inputs from NVIDIA Instant Replay: 0:a:0 system, 0:a:1 mic.
        if len(audio_streams) < 2:
            logger.warning("Fewer than 2 audio streams; skipping audio mixing")
            return None

        # Check if RNNoise is enabled via environment variable
        enable_rnnoise = os.environ.get("ENABLE_RNNOISE", "false").lower() == "true"
        rnnoise_model = "/app/models/rnnoise-model.rnnn"
        have_model = os.path.exists(rnnoise_model)

        # Determine mic processing filter
        if enable_rnnoise and have_model:
            logger.info(
                "RNNoise enabled - applying denoising to mic track before mixing"
            )
            mic_filter = f"arnndn=m={rnnoise_model},aformat=channel_layouts=stereo"
        else:
            if enable_rnnoise and not have_model:
                logger.warning(
                    "RNNoise enabled but model not found at %s; using raw mic",
                    rnnoise_model,
                )
            else:
                logger.info("RNNoise disabled - mixing raw mic with system audio")
            # Pass mic through without denoising, but ensure stereo
            mic_filter = "aformat=channel_layouts=stereo"

        # ---- Pass 0: create a temp file with the MIX ONLY (to measure loudness) ----
        # We'll generate the mixed track (system + denoised mic) to a temporary AAC file (no originals yet)
        import tempfile

        temp_dir = tempfile.gettempdir()
        mix_wav = os.path.join(temp_dir, "titanic_mix_for_measure.wav")

        # Filter graph: denoise mic -> mix with system
        filter_complex = (
            f"[0:a:1]{mic_filter}[mic];"
            f"[0:a:0][mic]amix=inputs=2:duration=longest:normalize=0,aformat=channel_layouts=stereo[a_mix]"  # keep headroom; no auto 1/n scaling
        )

        cmd_mix = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,
            "-filter_complex",
            filter_complex,
            "-map",
            "[a_mix]",
            "-c:a",
            "pcm_s16le",  # PCM for accurate measurement
            mix_wav,
        ]
        logger.debug("FFmpeg (build mix for measurement): %s", " ".join(cmd_mix))
        subprocess.run(cmd_mix, capture_output=True, text=True, check=True)

        # ---- Pass 1 (measure): EBU R128 on the mixed track (no output media, print JSON) ----
        # Typical streaming targets: I=-16, TP=-1.5, LRA=11 (tweak to taste).
        # We measure first to get accurate correction values.
        measure_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            mix_wav,
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",  # no file written; stats to stderr
        ]
        logger.debug("FFmpeg (loudnorm measure): %s", " ".join(measure_cmd))
        measure_run = subprocess.run(
            measure_cmd, capture_output=True, text=True, check=True
        )
        # loudnorm stats print to stderr; parse JSON block:
        import json
        import re

        stderr_output = measure_run.stderr
        logger.debug(
            "FFmpeg loudnorm stderr length: %d chars",
            len(stderr_output) if stderr_output else 0,
        )

        # Find JSON block containing loudnorm measurements
        # The JSON has keys like input_i, input_tp, etc. - find the block containing these
        json_match = None
        for m in re.finditer(r"\{[^{}]+\}", stderr_output):
            candidate = m.group(0)
            if '"input_i"' in candidate and '"input_tp"' in candidate:
                json_match = candidate
                break

        if not json_match:
            logger.error(
                "Failed to capture loudnorm measurement JSON. Stderr tail: %s",
                stderr_output[-1000:] if stderr_output else "(empty)",
            )
            return None

        logger.debug("Found loudnorm JSON: %s", json_match[:200])
        stats = json.loads(json_match)

        # ---- Pass 2 (apply): rebuild full file with 3 tracks, loudnorm on the MIX ONLY ----
        # We’ll redo the mix and apply loudnorm with measured_* values,
        # then include raw system & raw mic as separate tracks.
        # Keep video copied, set dispositions & metadata.
        ln = (
            f"loudnorm=I=-16:TP=-1.5:LRA=11:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true:print_format=summary"
        )

        filter_complex_full = (
            f"[0:a:1]{mic_filter}[mic];"
            f"[0:a:0][mic]amix=inputs=2:duration=longest:normalize=0,aformat=channel_layouts=stereo[a_mix_raw];"
            f"[a_mix_raw]{ln}[a_mix]"
        )

        # Set metadata title based on whether RNNoise was applied
        mic_description = "denoised mic" if (enable_rnnoise and have_model) else "mic"

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,
            "-filter_complex",
            filter_complex_full,
            # Video passthrough
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            # 1) NEW default loudness-normalized mixed track
            "-map",
            "[a_mix]",
            "-c:a:0",
            "aac",
            "-b:a:0",
            "256k",
            "-metadata:s:a:0",
            f"title=Default mix (system + {mic_description}, EBU R128)",
            "-metadata:s:a:0",
            "language=eng",
            "-disposition:a:0",
            "default",
            # 2) System only (raw) — optional map (won't fail if absent)
            "-map",
            "0:a:0?",
            "-c:a:1",
            "copy",
            "-metadata:s:a:1",
            "title=System only (raw)",
            "-metadata:s:a:1",
            "language=eng",
            "-disposition:a:1",
            "0",
            # 3) Mic only (raw)
            "-map",
            "0:a:1?",
            "-c:a:2",
            "copy",
            "-metadata:s:a:2",
            "title=Mic only (raw)",
            "-metadata:s:a:2",
            "language=eng",
            "-disposition:a:2",
            "0",
            "-movflags",
            "+faststart",
            output_file,
        ]

        logger.debug("FFmpeg (final build w/ loudnorm): %s", " ".join(cmd))
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info("Audio processing complete: %s", output_file)

        # cleanup temp
        try:
            os.remove(mix_wav)
        except Exception:
            pass

        return output_file

    except subprocess.CalledProcessError as e:
        logger.error("Audio processing failed: %s", e)
        logger.error("stderr: %s", e.stderr)
        return None
    except Exception as e:
        logger.error("Unexpected error during audio processing: %s", e)
        return None


def is_h265_video(input_file: str) -> bool:
    """
    Check if a video file is already encoded in H.265/HEVC.
    Returns True if the video is H.265, False otherwise.
    """
    codec = get_video_codec(input_file)
    if codec:
        # H.265 can be referred to as 'hevc', 'h265', or 'h.265'
        return codec in ["hevc", "h265", "h.265"]
    return False


def compress_video(input_file: str, target_size_mb: float | None = None):
    filename = os.path.basename(input_file)
    output_file = os.path.join(
        os.path.dirname(os.path.dirname(input_file)), "compressed", filename
    )

    logger.debug(f"Starting compression check: {input_file} -> {output_file}")

    temp_audio_processed = os.path.join(
        os.path.dirname(input_file), f"audio_processed_{filename}"
    )
    audio_processed_file = process_audio_with_rnnoise(input_file, temp_audio_processed)
    source_file = audio_processed_file if audio_processed_file else input_file

    if is_h265_video(source_file):
        logger.info("Video is already H.265, skipping video compression")
        if source_file != output_file:
            shutil.move(source_file, output_file)
        try:
            if audio_processed_file and input_file != source_file:
                os.remove(input_file)
        except FileNotFoundError:
            pass
        logger.info(f"Video processing complete: {filename}")
        if target_size_mb:
            shareable = build_shareable_copy(
                output_file, target_size_mb, os.path.dirname(output_file)
            )
            return [output_file, shareable]
        return output_file

    shareable_file = None
    try:
        try:
            # Probe number of audio streams for disposition logic
            audio_streams = get_audio_streams(source_file)
            num_audio_streams = len(audio_streams)
            logger.debug(f"Source file has {num_audio_streams} audio stream(s)")

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                source_file,
                # keep everything (video + all audio + any subs) in order
                "-map",
                "0",
                # video re-encode
                "-c:v",
                "libx265",
                "-crf",
                "22",
                "-preset",
                "medium",
                # force pools
                "-x265-params",
                _X265_PARAMS,
                "-tag:v",
                "hvc1",
                # copy audio/subs as-is
                "-c:a",
                "copy",
                "-c:s",
                "copy",
                "-movflags",
                "+faststart",
                "-map_metadata",
                "0",
            ]

            # Make the first audio track default (this is our loudnorm'd mix)
            if num_audio_streams >= 1:
                cmd += ["-disposition:a:0", "default"]
            # Clear others if present
            if num_audio_streams >= 2:
                cmd += ["-disposition:a:1", "0"]
            if num_audio_streams >= 3:
                cmd += ["-disposition:a:2", "0"]

            cmd += [output_file]

            logger.debug("FFmpeg (HEVC transcode): %s", " ".join(cmd))
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.debug(f"Video compression completed: {output_file}")
            logger.info(f"Video processing complete: {filename}")

        except subprocess.CalledProcessError as e:
            logger.error("Video compression failed: %s", e)
            logger.error("FFmpeg stderr: %s", e.stderr)
            # Fallback with ffmpeg-python
            out_opts = {"c:a": "copy", "map": "0", "x265-params": _X265_PARAMS}
            ffmpeg.input(source_file).output(
                output_file,
                vcodec="libx265",
                crf=22,
                preset="medium",
                **out_opts,
                vtag="hvc1",
                movflags="+faststart",
                map_metadata=0,
            ).run()

        # Size-targeted shareable copy from the same source (best quality). It's a
        # separate artifact — the full-quality output above is left untouched.
        if target_size_mb:
            shareable_file = build_shareable_copy(
                source_file, target_size_mb, os.path.dirname(output_file)
            )
    except Exception:
        # Primary and fallback transcode both failed — drop the partial/corrupt
        # output so it doesn't pile up in compressed/, then re-raise to fail the job.
        # Keep input_file (the original upload): there's no automatic retry and the
        # dependent upload won't run, so deleting it here would permanently lose the
        # user's video. Leaving it allows a manual/re-enqueued reprocess.
        remove_quietly(output_file)
        if shareable_file:
            remove_quietly(shareable_file)
        raise
    else:
        # Success — the compressed output is in place, so the original upload is no
        # longer needed. Remove it so orphaned files can't accumulate and fill the disk.
        remove_quietly(input_file)
    finally:
        # The audio-processed temp is regenerable scratch derived from input_file;
        # always clean it up, on success or failure.
        if audio_processed_file and source_file != input_file:
            remove_quietly(source_file)

    if target_size_mb and shareable_file:
        return [output_file, shareable_file]
    return output_file


def retry_with_exponential_backoff(
    max_retries=5, base_delay=1.0, max_delay=60.0, backoff_factor=2.0, jitter=True
):
    """
    Decorator that implements exponential backoff retry logic.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        backoff_factor: Factor by which delay increases each retry
        jitter: Whether to add random jitter to delay
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.RequestException,
                ) as e:
                    last_exception = e

                    # Check if this is a server error (5xx) that should be retried
                    if hasattr(e, "response") and e.response is not None:
                        if 500 <= e.response.status_code < 600:
                            should_retry = True
                        else:
                            # Don't retry client errors (4xx)
                            logger.error(
                                f"Client error, not retrying: {e.response.status_code}"
                            )
                            raise e
                    else:
                        should_retry = True

                    if attempt == max_retries or not should_retry:
                        logger.error(
                            f"Final attempt failed after {max_retries} retries: {str(e)}"
                        )
                        raise e

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (backoff_factor**attempt), max_delay)

                    # Add jitter to avoid thundering herd
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)

                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries + 1} failed: {str(e)}. Retrying in {delay:.2f}s"
                    )
                    time.sleep(delay)

            # This should never be reached, but just in case
            raise last_exception

        return wrapper

    return decorator


def _id_token_from_refresh(refresh_token: str, api_key: str) -> str:
    resp = requests.post(
        f"https://securetoken.googleapis.com/v1/token?key={api_key}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["id_token"]


def generate_fresh_auth_headers(job_meta):
    api_key = os.environ["FIREBASE_API_KEY"]
    refresh_token = job_meta.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Missing refresh_token in job meta")
    id_token = _id_token_from_refresh(refresh_token, api_key)
    return {"Authorization": f"Bearer {id_token}"}


@retry_with_exponential_backoff(max_retries=5, base_delay=1.0, max_delay=30.0)
def _send_video_to_umbrel(compressed_file, job):
    """Stream the file to the Umbrel server. Retried on transient network errors."""
    # Get Umbrel server URL from environment
    umbrel_url = os.environ.get("UMBREL_SERVER_URL", "http://umbrel:3029")

    # Upload to Umbrel server with automatic retry on failure
    logger.debug(f"Uploading video to Umbrel: {compressed_file}")

    # Generate fresh authentication headers to avoid token expiration
    user_uid = job.meta.get("user_uid")

    if not user_uid:
        logger.error("No user UID found in job metadata")
        raise ValueError("No user UID found in job metadata")

    logger.debug(f"Generating fresh auth headers for user: {user_uid}")

    auth_headers = generate_fresh_auth_headers(job.meta)

    # Extract folder from metadata if present
    folder = job.meta.get("X-Folder", None)
    safe_meta = {k: v for k, v in job.meta.items() if k not in ("refresh_token",)}
    logger.debug(f"Job meta: {safe_meta}")
    logger.debug(f"Extracted folder from metadata: {folder}")

    # Build the multipart form data manually to avoid memory issues
    # For large files, we need to stream the upload instead of loading into memory
    # Keep file open throughout the entire upload process
    file_handle = None
    try:
        file_handle = open(compressed_file, "rb")
        fields = {"file": (os.path.basename(compressed_file), file_handle, "video/mp4")}

        if folder:
            fields["folder"] = folder
            logger.debug(f"Uploading to folder: {folder}")
        else:
            logger.debug("No folder found in job metadata")

        # Create multipart encoder for streaming upload
        encoder = MultipartEncoder(fields=fields)

        # Monitor upload progress
        total_size = encoder.len
        logger.debug(f"Total upload size: {total_size / (1024 * 1024 * 1024):.2f} GB")

        # Only log progress when we cross a new 10% threshold to avoid spamming logs
        last_logged_percent = {"value": -1}

        def monitor_callback(monitor):
            progress = (monitor.bytes_read / total_size) * 100
            current_percent = int(progress // 10) * 10  # 0, 10, 20, ..., 100
            if current_percent != last_logged_percent["value"]:
                last_logged_percent["value"] = current_percent
                logger.debug(
                    f"Upload progress: {progress:.1f}% ({monitor.bytes_read / (1024 * 1024):.1f} MB / {total_size / (1024 * 1024):.1f} MB)"
                )

        monitor = MultipartEncoderMonitor(encoder, monitor_callback)

        auth_headers["Content-Type"] = monitor.content_type
        logger.debug("Uploading with fresh headers (Authorization, Content-Type)")

        upload_umbrel_url = umbrel_url + "/api/upload"

        # Increase timeout significantly for large files
        # For a 5GB file at 10MB/s, we need ~500 seconds, so use 1 hour to be safe
        timeout = 3600  # 1 hour timeout
        logger.debug(f"Using timeout of {timeout} seconds for upload")

        response = requests.post(
            upload_umbrel_url, data=monitor, headers=auth_headers, timeout=timeout
        )
        response.raise_for_status()

        response_data = response.json()
        logger.debug(f"Umbrel upload response: {response_data}")

        logger.debug(f"Successfully uploaded video to Umbrel: {compressed_file}")
        logger.info(f"Video uploaded to Umbrel: {os.path.basename(compressed_file)}")
    finally:
        if file_handle is not None:
            file_handle.close()
            logger.debug("Upload file handle closed")


def upload_video_to_umbrel(input_file=None):
    job = rq.get_current_job()

    # Try to get file from dependency job first, otherwise use the input parameter
    result = None
    try:
        if job.dependency:
            result = job.dependency.return_value(
                True
            )  # Get the result from the ffmpeg job
            logger.debug(f"Got file(s) from dependency job: {result}")
    except Exception:
        logger.warning(
            "Dependency job no longer exists in Redis, falling back to input_file"
        )

    if not result and input_file:
        result = input_file
        logger.debug(f"Using input file directly: {result}")
    elif not result:
        logger.error("No file provided - neither from dependency nor input parameter")
        raise ValueError("No file provided for upload")

    # compress_video returns a single path, or [full_quality, shareable] when a
    # target size was requested. Normalize to a list; the full-quality copy is
    # first so it lands even if the shareable one later fails.
    files = result if isinstance(result, list) else [result]

    for f in files:
        if not os.path.exists(f):
            logger.error(f"File not found: {f}")
            raise FileNotFoundError(f"File not found: {f}")

    try:
        for f in files:
            logger.debug(f"Starting upload to Umbrel for file: {f}")
            _send_video_to_umbrel(f, job)
    finally:
        # Always drop the local artifacts once we're done with them: delivered on
        # success, abandoned on permanently-failed retries. Either way, leaving them
        # behind leaks disk in videos/compressed and eventually triggers ENOSPC on
        # the next upload. The finally runs after all retries are exhausted, not
        # between them, so a retry never deletes a file it's about to re-send.
        for f in files:
            remove_quietly(f)
