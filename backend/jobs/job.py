import time
import ffmpeg
import logging
import rq
import os
logger = logging.getLogger(__name__)

# assume safe path already!
def compress_video(input_file: str) -> str:
    output_file = f'{input_file}_compressed.mp4'
    ffmpeg.input(input_file).output(
        output_file,
        vcodec='libx265',
        crf=22,
        preset='slow',
        acodec='copy',
        movflags='+faststart', # optimize for streaming, since this is for plex
        map_metadata=0         # preserve original metadata
    ).run()
    logger.debug(f"Compressed video saved to: {output_file}")
    logger.debug(f"returning {output_file}")
    # remove original file
    os.remove(input_file)
    return output_file

def upload_video_to_umbrel():
    job = rq.get_current_job()
    compressed_file = job.dependency.return_value(True)  # Get the result from the ffmpeg job
    logger.debug(f"Starting upload to Umbrel for file: {compressed_file}")
    
    if not os.path.exists(compressed_file):
        logger.error(f"Compressed file not found: {compressed_file}")
        raise FileNotFoundError(f"Compressed file not found: {compressed_file}")
    
    # Create a marker file to indicate upload status
    upload_dir = os.path.dirname(compressed_file)
    marker_file = os.path.join(upload_dir, f"uploaded_{os.path.basename(compressed_file)}")
    
    try:
        # Simulate upload process
        logger.debug(f"Uploading video to Umbrel: {compressed_file}")
        time.sleep(5)  # Simulate upload time
        
        # Create marker file to indicate successful upload
        with open(marker_file, "w") as f:
            f.write(f"Uploaded at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        logger.debug(f"Successfully uploaded video to Umbrel: {compressed_file}")
    except Exception as e:
        logger.error(f"Error uploading to Umbrel: {str(e)}")
        raise
    # remove compressed file
    os.remove(compressed_file)