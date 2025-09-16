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
from firebase_admin import auth, credentials

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
    
    # Check if video is already H.265
    if is_h265_video(input_file):
        logger.info(f"Video is already H.265, skipping compression: {input_file}")
        
        # Move the file to compressed directory without re-encoding
        import shutil
        shutil.move(input_file, output_file)
        logger.debug(f"Moved H.265 video to compressed directory: {output_file}")
        
        return output_file
    
    logger.debug(f"Video is not H.265, proceeding with compression: {input_file}")
    
    # Equivalent ffmpeg CLI command:
    # ffmpeg -i <input_file> -vcodec libx265 -crf 22 -preset medium -acodec copy -vtag hvc1 -movflags +faststart -map_metadata 0 <output_file>
    ffmpeg.input(input_file).output(
        output_file,
        vcodec='libx265',
        crf=22,
        preset='medium',
        acodec='copy',
        vtag='hvc1', # better support for Apple devices
        movflags='+faststart', # optimize for streaming, since this is for plex
        map_metadata=0         # preserve original metadata
    ).run()
    
    logger.debug(f"Compression completed: {output_file}")
    
    # Remove original uncompressed file
    os.remove(input_file)
    logger.debug(f"Removed original file: {input_file}")
    
    return output_file

import requests
import os

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

def generate_fresh_auth_headers(user_uid):
    initialize_firebase()

    custom_token = auth.create_custom_token(user_uid)
    if isinstance(custom_token, bytes):
        custom_token = custom_token.decode("utf-8")

    api_key = os.environ.get("FIREBASE_API_KEY")
    try:
        resp = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={api_key}",
            json={"token": custom_token, "returnSecureToken": True},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        print("Status:", resp.status_code)
        print("Body:", resp.text)
        raise
    id_token = resp.json()["idToken"]

    headers = {"Authorization": f"Bearer {id_token}"}
    return headers


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

    with open(compressed_file, 'rb') as f:
        files = {'file': (os.path.basename(compressed_file), f, 'video/mp4')}

        # Generate fresh authentication headers to avoid token expiration
        user_uid = job.meta.get('user_uid')
        
        if not user_uid:
            logger.error("No user UID found in job metadata")
            raise ValueError("No user UID found in job metadata")
        
        logger.debug(f"Generating fresh auth headers for user: {user_uid}")
        
        auth_headers = generate_fresh_auth_headers(user_uid)
        
        # Extract folder from metadata if present
        folder = job.meta.get('X-Folder', None)
        logger.debug(f"Job meta: {job.meta}")
        logger.debug(f"Extracted folder from metadata: {folder}")
        if folder:
            files['folder'] = (None, folder)  # Add folder as form field
            logger.debug(f"Uploading to folder: {folder}")
        else:
            logger.debug("No folder found in job metadata")

        logger.debug(f"Uploading with fresh headers: {[k for k in auth_headers.keys()]}")  # Log header keys only

        upload_umbrel_url = umbrel_url + '/api/upload'
        response = requests.post(upload_umbrel_url, files=files, headers=auth_headers, timeout=300)
        response.raise_for_status()

        response_data = response.json()
        logger.debug(f"Umbrel upload response: {response_data}")

    logger.debug(f"Successfully uploaded video to Umbrel: {compressed_file}")
    os.remove(compressed_file)
    logger.debug(f"Removed compressed file: {compressed_file}")
    
