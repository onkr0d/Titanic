import asyncio
from hypercorn.config import Config
from hypercorn.asyncio import serve
from flask import Flask, request, jsonify
from redis import Redis
from rq import Queue
import os
from werkzeug.utils import secure_filename
import logging
from flask_cors import CORS
from jobs.job import compress_video, upload_video_to_umbrel
import firebase_admin
from firebase_admin import credentials, auth, app_check
from functools import wraps
import shutil
import jwt
import flask

IS_DEV = os.environ.get('IS_DEV', 'false').lower() == 'true'
logger = logging.getLogger(__name__)
app = Flask(__name__)
origins = ["https://titanic.ivan.boston"]

logger.info(f"IS_DEV: {IS_DEV}")

if IS_DEV:
    origins.append("http://localhost:5173")
    origins.append("http://localhost:6969")

CORS(app,
     origins=origins,
     allow_headers=["Content-Type","Authorization","X-Firebase-AppCheck"],
     automatic_options=True)

logging.basicConfig(level=logging.DEBUG)

# Initialize Firebase Admin
cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin-sdk-cred.json'))
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)

# Configure upload settings
UPLOAD_FOLDER = os.path.abspath('videos')  # Base directory for videos
UNCOMPRESSED_FOLDER = os.path.join(UPLOAD_FOLDER, 'uncompressed')
COMPRESSED_FOLDER = os.path.join(UPLOAD_FOLDER, 'compressed')
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['UNCOMPRESSED_FOLDER'] = UNCOMPRESSED_FOLDER
app.config['COMPRESSED_FOLDER'] = COMPRESSED_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max file size, just for testing

# Ensure upload directories exist
os.makedirs(UNCOMPRESSED_FOLDER, exist_ok=True)
os.makedirs(COMPRESSED_FOLDER, exist_ok=True)

ffmpeg_queue = Queue('ffmpeg', connection=Redis())
umbrel_queue = Queue('umbrel', connection=Redis())

# TODO: set Firebase hosting IP to be static, so I can whitelist it in the backend??? 🤔
def verify_firebase_token(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if IS_DEV:
            return f(*args, **kwargs)
        
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'No authorization token provided'}), 401

        token = auth_header.split('Bearer ')[1]
        try:
            # Verify the ID token
            decoded_token = auth.verify_id_token(token)
            # Add the user info to the request context
            request.user = decoded_token
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Token verification failed: {str(e)}")
            return jsonify({'error': 'Invalid authorization token'}), 401        
    return decorated_function

@app.before_request
def verify_app_check() -> None:
    # no AppCheck for OPTIONS requests (CORS preflight)
    if request.method == 'OPTIONS':
        return
    
    app_check_token = flask.request.headers.get("X-Firebase-AppCheck", default="")
    try:
        app_check.verify_token(app_check_token)
        # If verify_token() succeeds, okay to continue to route handler.
    except (ValueError, jwt.exceptions.DecodeError):
        logger.error(f"App Check token verification failed: {app_check_token}")
        flask.abort(401)
    
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

@app.route("/upload", methods=['POST'])
@verify_firebase_token
def upload_video():
    try:
        logger.debug(f"Received upload request from user: {request.user.get('email', 'unknown')}")
        if 'file' not in request.files:
            logger.error("No file part in request")
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        should_compress = request.form.get('shouldCompress', 'true').lower() == 'true'
        logger.debug(f"Received file: {file.filename}, compression: {should_compress}")
        
        if file.filename == '':
            logger.error("Empty filename")
            return jsonify({'error': 'No selected file'}), 400
        
        if file and allowed_file(file.filename):
            # Secure the filename and create full path
            filename = secure_filename(file.filename)
            target_dir = app.config['UNCOMPRESSED_FOLDER']
            filepath = os.path.join(target_dir, filename)
            
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
                    filepath = os.path.join(target_dir, f"{base}_{counter}{ext}")
                    counter += 1
                
                file.save(filepath)
                logger.debug("File saved successfully")
                
                # Enqueue the video processing job only if compression is enabled
                if should_compress:
                    ffmpeg_job = ffmpeg_queue.enqueue(compress_video, args=[filepath])
                    umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, depends_on=ffmpeg_job)
                else:
                    # If compression is disabled, just upload the original file
                    umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, args=[filepath])
                
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

@app.route('/api/health')
@verify_firebase_token
def health_check():
    return jsonify({"status": "ok"}), 200

@app.route('/space')
@verify_firebase_token
def space():
    # check how much disk space is left
    total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
    return jsonify({"total": total, "used": used, "free": free}), 200

@app.route('/health')
def docker_health_check():
    """Unauthenticated health check endpoint for Docker"""
    return jsonify({"status": "healthy"}), 200

# FIXME: why are some of them using /api/ and others are not ?!

if __name__ == "__main__":
    # app.run(host="0.0.0.0", port=5000, debug=True)
    asyncio.run(serve(app, Config.from_toml("hypercorn.toml")))