import time
import ffmpeg
import logging
import rq
import os
import json
import subprocess

# This configures logging for any process that imports this module.
# It's safe to call here because logging.basicConfig() does nothing
# if a handler is already configured for the root logger.
# This will ensure that RQ workers have logging configured.
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

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
    # ffmpeg -i <input_file> -vcodec libx265 -crf 22 -preset medium -acodec copy -movflags +faststart -map_metadata 0 <output_file>
    ffmpeg.input(input_file).output(
        output_file,
        vcodec='libx265',
        crf=22,
        preset='medium',
        acodec='copy',
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
    umbrel_url = os.environ.get('UMBREL_SERVER_URL', 'http://umbrel:3000/api/upload')

    try:
        # Upload to Umbrel server
        logger.debug(f"Uploading video to Umbrel: {compressed_file}")
        
        with open(compressed_file, 'rb') as f:
            files = {'file': (os.path.basename(compressed_file), f, 'video/mp4')}
            
            auth_headers = job.meta
            logger.debug(f"Uploading with headers: {auth_headers}")
            response = requests.post(umbrel_url, files=files, headers=auth_headers, timeout=300)
            response.raise_for_status()
            
            response_data = response.json()
            logger.debug(f"Umbrel upload response: {response_data}")
        
        logger.debug(f"Successfully uploaded video to Umbrel: {compressed_file}")
        os.remove(compressed_file)
        logger.debug(f"Removed compressed file: {compressed_file}")    
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error uploading to Umbrel: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error uploading to Umbrel: {str(e)}")
        raise
    
