import asyncio

import requests
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
    origins.append("http://localhost:5002")

CORS(app,
     origins=origins,
     allow_headers=["Content-Type","Authorization","X-Firebase-AppCheck", "baggage", "sentry-trace"],
     automatic_options=True)

# Initialize Firebase Admin
cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin-sdk-cred.json'))
cred = credentials.Certificate(cred_path)

# wait! before we initialize Firebase, we need to prevent it from spamming us with debug:
logging.getLogger("cachecontrol").setLevel(logging.WARNING)
logging.getLogger("cachecontrol.controller").setLevel(logging.WARNING)

logging.getLogger("google.auth").setLevel(logging.WARNING)
logging.getLogger("google.auth.transport").setLevel(logging.WARNING)

firebase_admin.initialize_app(cred)

# Configure upload settings
UPLOAD_FOLDER = os.path.abspath('videos')  # Base directory for videos
UNCOMPRESSED_FOLDER = os.path.join(UPLOAD_FOLDER, 'uncompressed')
COMPRESSED_FOLDER = os.path.join(UPLOAD_FOLDER, 'compressed')
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv', 'm4v', 'avi', 'webm', 'ts'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['UNCOMPRESSED_FOLDER'] = UNCOMPRESSED_FOLDER
app.config['COMPRESSED_FOLDER'] = COMPRESSED_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10GB max file size

# Ensure upload directories exist
try:
    os.makedirs(UNCOMPRESSED_FOLDER, exist_ok=True)
    os.makedirs(COMPRESSED_FOLDER, exist_ok=True)
except PermissionError as e:
    logger.warning(f"Could not create upload directories: {e}. They may already exist with correct permissions.")
    # Check if directories exist and are writable
    if not os.path.exists(UNCOMPRESSED_FOLDER) or not os.access(UNCOMPRESSED_FOLDER, os.W_OK):
        logger.error(f"Upload directory {UNCOMPRESSED_FOLDER} is not accessible")
        raise
    if not os.path.exists(COMPRESSED_FOLDER) or not os.access(COMPRESSED_FOLDER, os.W_OK):
        logger.error(f"Compressed directory {COMPRESSED_FOLDER} is not accessible")
        raise

ffmpeg_queue = Queue('ffmpeg', connection=Redis(), default_timeout=3600)
umbrel_queue = Queue('umbrel', connection=Redis(), default_timeout=3600)

# TODO: set Firebase hosting IP to be static, so I can whitelist it in the backend??? 🤔
def verify_firebase_token(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if IS_DEV:
            # When in development mode, we bypass token verification and mock the user.
            request.user = {'email': 'dev@example.com', 'uid': 'dev-user'}
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
    
    # no AppCheck for health endpoint (Docker health checks)
    if request.path == '/health':
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

@app.route("/api/upload", methods=['POST'])
@verify_firebase_token
def upload_video():
    try:
        logger.debug(f"Received upload request from user: {request.user.get('email', 'unknown')}")
        if 'file' not in request.files:
            logger.error("No file part in request")
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        should_compress = request.form.get('shouldCompress', 'true').lower() == 'true'
        folder = request.form.get('folder', '').strip()  # Get folder parameter, default to empty
        folder = folder if folder else None  # Convert empty string to None
        logger.debug(f"Received file: {file.filename}, compression: {should_compress}, folder: {folder}")
        
        if file.filename == '':
            logger.error("Empty filename")
            return jsonify({'error': 'No selected file'}), 400
        
        if file and allowed_file(file.filename):
            # Secure the filename and create full path
            filename = secure_filename(file.filename)
            filename = filename.replace("_", " ") # undo stupid whitespace -> _ replacement
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
                headers = {
                    'Authorization': request.headers.get('Authorization'),
                    'X-Firebase-AppCheck': request.headers.get('X-Firebase-AppCheck')
                }

                # Include folder information in the headers for the Umbrel job
                if folder:
                    headers['X-Folder'] = folder
                    logger.debug(f"Including folder in upload: {folder}")
                else:
                    logger.debug("No folder specified for upload")

                if should_compress:
                    ffmpeg_job = ffmpeg_queue.enqueue(compress_video, args=[filepath])
                    # give the umbrel job the auth headers so the umbrel server can verify them
                    umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, depends_on=ffmpeg_job, meta=headers)
                else:
                    # If compression is disabled, just upload the original file
                    umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, args=[filepath], meta=headers)
                
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

@app.route('/api/space')
@verify_firebase_token
def space():
    # check how much disk space is left
    total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
    return jsonify({"total": total, "used": used, "free": free}), 200

@app.route('/api/folders')
@verify_firebase_token
def get_folders():
    """Get available folders from Umbrel server"""
    try:
        logger.debug("Fetching folders from Umbrel server")
        # Build folders URL from base server URL
        umbrel_base_url = os.environ.get('UMBREL_SERVER_URL', 'http://100.97.35.4:3029')
        
        umbrel_url = umbrel_base_url + '/api/folders'

        # Forward the authorization headers
        headers = {
            'Authorization': request.headers.get('Authorization'),
            'X-Firebase-AppCheck': request.headers.get('X-Firebase-AppCheck')
        }

        response = requests.get(umbrel_url, headers=headers, timeout=30)
        response.raise_for_status()

        folders_data = response.json()
        logger.debug(f"Retrieved folders: {folders_data}")

        return jsonify(folders_data), 200

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching folders from Umbrel: {str(e)}")
        return jsonify({'error': f'Failed to fetch folders: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Unexpected error fetching folders: {str(e)}")
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/api/sentry-proxy', methods=['POST'])
def sentry_proxy():
    """Proxy endpoint for Sentry API requests to avoid adblocker detection"""
    try:
        # Extract the Sentry DSN from request headers or use environment variable
        sentry_dsn = request.headers.get('X-Sentry-DSN')
        if not sentry_dsn:
            return jsonify({'error': 'Missing Sentry DSN'}), 400
        
        # Parse DSN to extract project ID and endpoint
        # DSN format: https://key@organization.ingest.sentry.io/project_id
        import re
        dsn_match = re.match(r'https://([^@]+)@([^/]+)/(\d+)', sentry_dsn)
        if not dsn_match:
            return jsonify({'error': 'Invalid Sentry DSN format'}), 400
        
        key, org_host, project_id = dsn_match.groups()
        
        # Build the actual Sentry API endpoint
        sentry_url = f'https://{org_host}/api/{project_id}/envelope/'
        
        # Forward the request to Sentry
        headers = {
            'Content-Type': request.headers.get('Content-Type', 'application/x-sentry-envelope'),
            'User-Agent': request.headers.get('User-Agent', 'Titanic-Sentry-Proxy/1.0'),
        }
        
        # Add authentication header
        headers['X-Sentry-Auth'] = f'Sentry sentry_version=7, sentry_key={key}, sentry_client=sentry.javascript.react/9.40.0'
        
        logger.debug(f"Proxying Sentry request to: {sentry_url}")
        
        response = requests.post(
            sentry_url,
            data=request.get_data(),
            headers=headers,
            timeout=30
        )
        
        # Return the response from Sentry
        return response.content, response.status_code, {
            'Content-Type': response.headers.get('Content-Type', 'application/json')
        }
        
    except Exception as e:
        logger.error(f"Error proxying Sentry request: {str(e)}")
        return jsonify({'error': f'Proxy error: {str(e)}'}), 500

@app.route('/health')
def docker_health_check():
    """Unauthenticated health check endpoint for Docker"""
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    asyncio.run(serve(app, Config.from_toml("hypercorn.toml")))