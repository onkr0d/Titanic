from flask import Flask, request, jsonify
from redis import Redis
from rq import Queue
import os
from werkzeug.utils import secure_filename
import logging
from flask_cors import CORS
from pathlib import Path
from jobs.job import compress_video, upload_video_to_umbrel
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes 
# FIME: don't do that ^
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configure upload settings
UPLOAD_FOLDER = os.path.abspath('uploads')  # Convert to absolute path
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max file size, just for testing

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ffmpeg_queue = Queue('ffmpeg', connection=Redis())
umbrel_queue = Queue('umbrel', connection=Redis())

def allowed_file(filename):
    # Check for null bytes
    if '\0' in filename:
        return False
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_safe_path(filepath):
    """
    Check if the filepath is safe and within the upload directory.
    Prevents directory traversal attacks.
    """
    try:
        # Convert to absolute path
        abs_path = os.path.abspath(filepath)
        # Check if the path starts with the upload folder
        return abs_path.startswith(UPLOAD_FOLDER)
    except Exception:
        return False

@app.route("/")
def home():
    # this is the backend! gg!
    return "Secure HTTPS server running!"

@app.route("/upload", methods=['POST'])
def upload_video():
    try:
        logger.debug("Received upload request")
        if 'file' not in request.files:
            logger.error("No file part in request")
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        logger.debug(f"Received file: {file.filename}")
        
        if file.filename == '':
            logger.error("Empty filename")
            return jsonify({'error': 'No selected file'}), 400
        
        if file and allowed_file(file.filename):
            # Secure the filename and create full path
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            # Additional security check
            if not is_safe_path(filepath):
                logger.error(f"Invalid file path: {filepath}")
                return jsonify({'error': 'Invalid file path'}), 400
            
            logger.debug(f"Saving file to: {filepath}")
            
            try:
                # Create a unique filename to prevent overwriting
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(filepath):
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{base}_{counter}{ext}")
                    counter += 1
                
                file.save(filepath)
                logger.debug("File saved successfully")
                
                # Enqueue the video processing job
                ffmpeg_job = ffmpeg_queue.enqueue(compress_video, args=[filepath])
                umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, depends_on=ffmpeg_job)
                
                return jsonify({
                    'message': 'File uploaded successfully',
                    'filename': os.path.basename(filepath)
                }), 200
            except Exception as e:
                logger.error(f"Error saving file: {str(e)}")
                return jsonify({'error': f'Error saving file: {str(e)}'}), 500
        
        logger.error(f"Invalid file type: {file.filename}")
        return jsonify({'error': 'Invalid file type'}), 400
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
