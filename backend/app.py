from flask import Flask, request, jsonify
from redis import Redis
from rq import Queue
from jobs.long_job import super_long_runtime_function
import os
from werkzeug.utils import secure_filename
import logging
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes 
# FIME: don't do that ^
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configure upload settings
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max file size, just for testing

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ffmpeg_queue = Queue('ffmpeg', connection=Redis())
umbrel_queue = Queue('umbrel', connection=Redis())

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/")
def home():
    # this is the backend! gg!
    ffmpeg_job = ffmpeg_queue.enqueue(super_long_runtime_function)
    umbrel_queue.enqueue(super_long_runtime_function, depends_on=ffmpeg_job)
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
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            logger.debug(f"Saving file to: {filepath}")
            
            try:
                file.save(filepath)
                logger.debug("File saved successfully")
                
                # Enqueue the video processing job
                # ffmpeg_job = ffmpeg_queue.enqueue(super_long_runtime_function, args=[filepath])
                # umbrel_queue.enqueue(super_long_runtime_function, args=[filepath], depends_on=ffmpeg_job)
                
                return jsonify({
                    'message': 'File uploaded successfully',
                    'filename': filename
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
