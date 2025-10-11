import requests
import time
import ffmpeg
import logging
import rq
import os
import json
import subprocess
import random
import functools
import firebase_admin
import shutil
from firebase_admin import credentials
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor

# This configures logging for any process that imports this module.
# It's safe to call here because logging.basicConfig() does nothing
# if a handler is already configured for the root logger.
# This will ensure that RQ workers have logging configured.
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def initialize_firebase():
    """
    Initialize Firebase Admin SDK if not already initialized.
    This is needed because RQ workers run in separate processes.
    """
    try:
        # Check if Firebase is already initialized
        firebase_admin.get_app()
        logger.debug("Firebase app already initialized")
    except ValueError:
        # Firebase not initialized, so initialize it
        logger.debug("Initializing Firebase app for job worker")

        # Get credentials path from environment or use default
        cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS',
                                   os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                               'admin-sdk-cred.json'))

        if not os.path.exists(cred_path):
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
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-select_streams', 'v:0',  # Only video streams
            input_file
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        if data['streams']:
            codec_name = data['streams'][0]['codec_name'].lower()
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

def get_audio_streams(input_file: str) -> list:
    """
    Get all audio streams in a video file using ffprobe.
    Returns a list of audio stream information.
    """
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-select_streams', 'a',  # All audio streams
            input_file
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        audio_streams = data.get('streams', [])
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
        enable_rnnoise = os.environ.get('ENABLE_RNNOISE', 'false').lower() == 'true'
        rnnoise_model = "/app/models/rnnoise-model.rnnn"
        have_model = os.path.exists(rnnoise_model)

        # Determine mic processing filter
        if enable_rnnoise and have_model:
            logger.info("RNNoise enabled - applying denoising to mic track before mixing")
            mic_filter = f"arnndn=m={rnnoise_model},aformat=channel_layouts=stereo"
        else:
            if enable_rnnoise and not have_model:
                logger.warning("RNNoise enabled but model not found at %s; using raw mic", rnnoise_model)
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
            "ffmpeg", "-y",
            "-i", input_file,
            "-filter_complex", filter_complex,
            "-map", "[a_mix]", "-c:a", "pcm_s16le",  # PCM for accurate measurement
            mix_wav
        ]
        logger.debug("FFmpeg (build mix for measurement): %s", " ".join(cmd_mix))
        subprocess.run(cmd_mix, capture_output=True, text=True, check=True)

        # ---- Pass 1 (measure): EBU R128 on the mixed track (no output media, print JSON) ----
        # Typical streaming targets: I=-16, TP=-1.5, LRA=11 (tweak to taste).
        # We measure first to get accurate correction values.
        measure_cmd = [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", mix_wav,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-"  # no file written; stats to stderr
        ]
        logger.debug("FFmpeg (loudnorm measure): %s", " ".join(measure_cmd))
        measure_run = subprocess.run(measure_cmd, capture_output=True, text=True, check=True)
        # loudnorm stats print to stderr; parse JSON block:
        import re, json
        m = re.search(r"\{[\s\S]*?\}\s*$", measure_run.stderr)
        if not m:
            logger.error("Failed to capture loudnorm measurement JSON")
            return None
        stats = json.loads(m.group(0))

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
            "ffmpeg", "-y",
            "-i", input_file,
            "-filter_complex", filter_complex_full,

            # Video passthrough
            "-map", "0:v:0", "-c:v", "copy",

            # 1) NEW default loudness-normalized mixed track
            "-map", "[a_mix]", "-c:a:0", "aac", "-b:a:0", "256k",
            "-metadata:s:a:0", f"title=Default mix (system + {mic_description}, EBU R128)",
            "-metadata:s:a:0", "language=eng",
            "-disposition:a:0", "default",

            # 2) System only (raw) — optional map (won't fail if absent)
            "-map", "0:a:0?", "-c:a:1", "copy",
            "-metadata:s:a:1", "title=System only (raw)",
            "-metadata:s:a:1", "language=eng",
            "-disposition:a:1", "0",

            # 3) Mic only (raw)
            "-map", "0:a:1?", "-c:a:2", "copy",
            "-metadata:s:a:2", "title=Mic only (raw)",
            "-metadata:s:a:2", "language=eng",
            "-disposition:a:2", "0",

            "-movflags", "+faststart",
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
        return codec in ['hevc', 'h265', 'h.265']
    return False

def compress_video(input_file: str) -> str:
    filename = os.path.basename(input_file)
    output_file = os.path.join(os.path.dirname(os.path.dirname(input_file)), 'compressed', filename)

    logger.debug(f"Starting compression check: {input_file} -> {output_file}")

    temp_audio_processed = os.path.join(os.path.dirname(input_file), f"audio_processed_{filename}")
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
        return output_file

    try:
        # Probe number of audio streams for disposition logic
        audio_streams = get_audio_streams(source_file)
        num_audio_streams = len(audio_streams)
        logger.debug(f"Source file has {num_audio_streams} audio stream(s)")

        cmd = [
            "ffmpeg", "-y",
            "-i", source_file,

            # keep everything (video + all audio + any subs) in order
            "-map", "0",

            # video re-encode
            "-c:v", "libx265", "-crf", "22", "-preset", "medium", "-tag:v", "hvc1",

            # copy audio/subs as-is
            "-c:a", "copy",
            "-c:s", "copy",

            "-movflags", "+faststart",
            "-map_metadata", "0",
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

    except subprocess.CalledProcessError as e:
        logger.error("Video compression failed: %s", e)
        logger.error("FFmpeg stderr: %s", e.stderr)
        # Fallback with ffmpeg-python
        out_opts = {'c:a': 'copy', 'map': '0'}
        ffmpeg.input(source_file).output(
            output_file,
            vcodec='libx265',
            crf=22,
            preset='medium',
            **out_opts,
            vtag='hvc1',
            movflags='+faststart',
            map_metadata=0
        ).run()

    # Cleanup temps/original
    try:
        if audio_processed_file and source_file != input_file:
            os.remove(source_file)
    except FileNotFoundError:
        pass
    try:
        os.remove(input_file)
    except FileNotFoundError:
        pass

    return output_file

def retry_with_exponential_backoff(
    max_retries=5,
    base_delay=1.0,
    max_delay=60.0,
    backoff_factor=2.0,
    jitter=True
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
                except (requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout,
                        requests.exceptions.RequestException) as e:
                    last_exception = e

                    # Check if this is a server error (5xx) that should be retried
                    if hasattr(e, 'response') and e.response is not None:
                        if 500 <= e.response.status_code < 600:
                            should_retry = True
                        else:
                            # Don't retry client errors (4xx)
                            logger.error(f"Client error, not retrying: {e.response.status_code}")
                            raise e
                    else:
                        should_retry = True

                    if attempt == max_retries or not should_retry:
                        logger.error(f"Final attempt failed after {max_retries} retries: {str(e)}")
                        raise e

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)

                    # Add jitter to avoid thundering herd
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)

                    logger.warning(f"Attempt {attempt + 1}/{max_retries + 1} failed: {str(e)}. Retrying in {delay:.2f}s")
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
def upload_video_to_umbrel(input_file=None):
    job = rq.get_current_job()
    
    # Try to get file from dependency job first, otherwise use the input parameter
    if job.dependency:
        compressed_file = job.dependency.return_value(True)  # Get the result from the ffmpeg job
        logger.debug(f"Got file from dependency job: {compressed_file}")
    elif input_file:
        compressed_file = input_file
        logger.debug(f"Using input file directly: {compressed_file}")
    else:
        logger.error("No file provided - neither from dependency nor input parameter")
        raise ValueError("No file provided for upload")
    
    logger.debug(f"Starting upload to Umbrel for file: {compressed_file}")
    
    if not os.path.exists(compressed_file):
        logger.error(f"File not found: {compressed_file}")
        raise FileNotFoundError(f"File not found: {compressed_file}")
    
    # Get Umbrel server URL from environment
    umbrel_url = os.environ.get('UMBREL_SERVER_URL', 'http://100.97.35.4:3029') # tailsale ip

    # Upload to Umbrel server with automatic retry on failure
    logger.debug(f"Uploading video to Umbrel: {compressed_file}")

    # Generate fresh authentication headers to avoid token expiration
    user_uid = job.meta.get('user_uid')
    
    if not user_uid:
        logger.error("No user UID found in job metadata")
        raise ValueError("No user UID found in job metadata")
    
    logger.debug(f"Generating fresh auth headers for user: {user_uid}")
    
    auth_headers = generate_fresh_auth_headers(job.meta)
    
    # Extract folder from metadata if present
    folder = job.meta.get('X-Folder', None)
    logger.debug(f"Job meta: {job.meta}")
    logger.debug(f"Extracted folder from metadata: {folder}")
    
    # Build the multipart form data manually to avoid memory issues
    # For large files, we need to stream the upload instead of loading into memory
    # Keep file open throughout the entire upload process
    file_handle = None
    try:
        file_handle = open(compressed_file, 'rb')
        fields = {
            'file': (os.path.basename(compressed_file), file_handle, 'video/mp4')
        }

        if folder:
            fields['folder'] = folder
            logger.debug(f"Uploading to folder: {folder}")
        else:
            logger.debug("No folder found in job metadata")
        
        # Create multipart encoder for streaming upload
        encoder = MultipartEncoder(fields=fields)
        
        # Monitor upload progress
        total_size = encoder.len
        logger.debug(f"Total upload size: {total_size / (1024*1024*1024):.2f} GB")
        
        # Only log progress when we cross a new 10% threshold to avoid spamming logs
        last_logged_percent = {'value': -1}
        def monitor_callback(monitor):
            progress = (monitor.bytes_read / total_size) * 100
            current_percent = int(progress // 10) * 10  # 0, 10, 20, ..., 100
            if current_percent != last_logged_percent['value']:
                last_logged_percent['value'] = current_percent
                logger.debug(f"Upload progress: {progress:.1f}% ({monitor.bytes_read / (1024*1024):.1f} MB / {total_size / (1024*1024):.1f} MB)")
        
        monitor = MultipartEncoderMonitor(encoder, monitor_callback)
        
        auth_headers['Content-Type'] = monitor.content_type
        logger.debug(f"Uploading with fresh headers: {[k for k in auth_headers.keys()]}")  # Log header keys only

        upload_umbrel_url = umbrel_url + '/api/upload'
        
        # Increase timeout significantly for large files
        # For a 5GB file at 10MB/s, we need ~500 seconds, so use 1 hour to be safe
        timeout = 3600  # 1 hour timeout
        logger.debug(f"Using timeout of {timeout} seconds for upload")
        
        response = requests.post(upload_umbrel_url, data=monitor, headers=auth_headers, timeout=timeout)
        response.raise_for_status()

        response_data = response.json()
        logger.debug(f"Umbrel upload response: {response_data}")

        logger.debug(f"Successfully uploaded video to Umbrel: {compressed_file}")
    finally:
        if file_handle is not None:
            file_handle.close()
            logger.debug("Upload file handle closed")
    
    # Remove the compressed file after successful upload
    # Use try/except to avoid race condition
    try:
        os.remove(compressed_file)
        logger.debug(f"Removed compressed file: {compressed_file}")
    except FileNotFoundError:
        logger.debug(f"Compressed file already removed: {compressed_file}")
    except Exception as e:
        logger.warning(f"Error removing compressed file: {e}")
    
