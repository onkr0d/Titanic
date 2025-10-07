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

# TODO: Might need to normalize audio streams as well...
def process_audio_with_rnnoise(input_file: str, output_file: str) -> str:
    """
    Mix the 2nd audio stream (mic, index 1) with the 1st stream (system/game) 
    to create a new DEFAULT track, and preserve both original audio streams untouched.
    
    If ENABLE_RNNOISE env var is set to 'true', applies RNNoise denoising to the mic
    before mixing. Otherwise, mixes the raw mic audio.
    
    Video is copied through.
    """
    try:
        # Probe audio streams
        audio_streams = get_audio_streams(input_file)
        if not audio_streams:
            logger.warning("No audio streams; skipping audio processing")
            return None
        if len(audio_streams) < 2:
            logger.warning("Fewer than 2 audio streams; skipping audio mixing")
            return None

        # Check if RNNoise is enabled via environment variable
        enable_rnnoise = os.environ.get('ENABLE_RNNOISE', 'false').lower() == 'true'
        rnnoise_model = "/app/models/rnnoise-model.rnnn"
        have_model = os.path.exists(rnnoise_model)

        # Build filter graph:
        # Step 1: Process mic audio (with or without RNNoise)
        # Step 2: Mix processed mic with system audio
        if enable_rnnoise and have_model:
            logger.info("RNNoise enabled - applying denoising to mic track before mixing")
            # Apply RNNoise denoising to mic track
            denoise = f"[0:a:1]arnndn=m={rnnoise_model}[mic];"
        else:
            if enable_rnnoise and not have_model:
                logger.warning("RNNoise enabled but model not found at %s; using raw mic", rnnoise_model)
            else:
                logger.info("RNNoise disabled - mixing raw mic with system audio")
            # Pass mic through without denoising
            denoise = f"[0:a:1]anull[mic];"

        # Mix system audio with (possibly denoised) mic audio
        filter_complex = (
            denoise +
            "[0:a:0][mic]amix=inputs=2:duration=longest:normalize=0[a_mix]"
        )

        # Assemble ffmpeg command
        cmd = [
            "ffmpeg", "-y",
            "-i", input_file,
            "-filter_complex", filter_complex,

            # Video: passthrough
            "-map", "0:v:0", "-c:v", "copy",

            # 1) New default mixed track (AAC)
            "-map", "[a_mix]", "-c:a:0", "aac", "-b:a:0", "256k",
            "-metadata:s:a:0", "title=Default mix (system + denoised mic)",
            "-metadata:s:a:0", "language=eng",
            "-disposition:a:0", "default",

            # 2) Original system audio (copy)
            "-map", "0:a:0", "-c:a:1", "copy",
            "-metadata:s:a:1", "title=System only (raw)",
            "-metadata:s:a:1", "language=eng",
            "-disposition:a:1", "0",

            # 3) Original mic audio (copy)
            "-map", "0:a:1", "-c:a:2", "copy",
            "-metadata:s:a:2", "title=Mic only (raw)",
            "-metadata:s:a:2", "language=eng",
            "-disposition:a:2", "0",

            "-movflags", "+faststart",
            output_file,
        ]

        logger.debug("Running FFmpeg: %s", " ".join(cmd))
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info("Audio processing complete: %s", output_file)
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

# assume safe path already!
def compress_video(input_file: str) -> str:
    # Get the base filename without the directory
    filename = os.path.basename(input_file)
    # Create output path in the compressed directory
    output_file = os.path.join(os.path.dirname(os.path.dirname(input_file)), 'compressed', filename)
    
    logger.debug(f"Starting compression check: {input_file} -> {output_file}")
    
    # Step 1: Process audio with RNNoise and dynamic range compression
    # Create temporary file for audio-processed video
    temp_audio_processed = os.path.join(os.path.dirname(input_file), f"audio_processed_{filename}")
    
    audio_processed_file = process_audio_with_rnnoise(input_file, temp_audio_processed)
    
    # Determine which file to use for video compression
    # If audio processing succeeded, use the audio-processed file; otherwise, use original
    source_file = audio_processed_file if audio_processed_file else input_file
    
    # Step 2: Check if video is already H.265
    if is_h265_video(source_file):
        logger.info(f"Video is already H.265, skipping video compression")
        
        # If we processed audio, the source_file is already the processed one
        # Just move it to the output location
        if source_file != output_file:
            shutil.move(source_file, output_file)
        logger.debug(f"Moved processed video to compressed directory: {output_file}")
        
        # Clean up original if different from source
        # Use try/except to avoid race condition (TOCTOU) with os.path.exists
        try:
            if audio_processed_file and input_file != source_file:
                os.remove(input_file)
                logger.debug(f"Removed original file: {input_file}")
        except FileNotFoundError:
            logger.debug(f"Original file already removed: {input_file}")
        except Exception as e:
            logger.warning(f"Error removing original file: {e}")
        
        return output_file
    
    logger.debug(f"Video is not H.265, proceeding with video compression")
    
    # Step 3: Compress video (audio already processed, so copy audio streams)
    try:
        # Use subprocess for more control over ffmpeg
        cmd = [
            'ffmpeg',
            '-i', source_file,
            '-vcodec', 'libx265',
            '-crf', '22',
            '-preset', 'medium',
            '-c:a', 'copy',  # Copy already-processed audio
            '-vtag', 'hvc1',  # Better support for Apple devices
            '-movflags', '+faststart',  # Optimize for streaming
            '-map_metadata', '0',  # Preserve original metadata
            '-y',  # Overwrite output
            output_file
        ]
        
        logger.debug(f"Running video compression command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.debug(f"Video compression completed: {output_file}")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Video compression failed: {e}")
        logger.error(f"FFmpeg stderr: {e.stderr}")
        # Fall back to original method if subprocess fails
        logger.info("Falling back to ffmpeg-python library")
        ffmpeg.input(source_file).output(
            output_file,
            vcodec='libx265',
            crf=22,
            preset='medium',
            **{'c:a': 'copy'},
            vtag='hvc1',
            movflags='+faststart',
            map_metadata=0
        ).run()
    
    # Clean up temporary and original files
    # Use try/except to avoid race condition (TOCTOU) with os.path.exists
    try:
        if audio_processed_file and source_file != input_file:
            os.remove(source_file)
            logger.debug(f"Removed temporary audio-processed file: {source_file}")
    except FileNotFoundError:
        logger.debug(f"Temporary file already removed: {source_file}")
    except Exception as e:
        logger.warning(f"Error removing temporary file: {e}")
    
    # Remove original input file
    try:
        os.remove(input_file)
        logger.debug(f"Removed original file: {input_file}")
    except FileNotFoundError:
        logger.debug(f"Original file already removed: {input_file}")
    except Exception as e:
        logger.warning(f"Error removing original file: {e}")
    
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
    file_handle = open(compressed_file, 'rb')
    
    try:
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
        
        def monitor_callback(monitor):
            progress = (monitor.bytes_read / total_size) * 100
            if int(progress) % 10 == 0:  # Log every 10%
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
        # Always close the file handle
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
    
