import asyncio

import requests
from hypercorn.config import Config
from hypercorn.asyncio import serve
from quart import Quart, request, jsonify
from quart_cors import cors
from redis import Redis
from rq import Queue
import os
from quart import abort
from werkzeug.utils import secure_filename
import logging
from jobs.job import compress_video, upload_video_to_umbrel
import firebase_admin
from firebase_admin import credentials, auth, app_check
from functools import wraps
import shutil
import jwt
import aiofiles

IS_DEV = os.environ.get('IS_DEV', 'false').lower() == 'true'
logger = logging.getLogger(__name__)
app = Quart(__name__)
origins = ["https://titanic.ivan.boston"]

logger.info(f"IS_DEV: {IS_DEV}")

if IS_DEV:
    origins.append("http://localhost:5173")
    origins.append("http://localhost:5174")
    origins.append("http://localhost:6969")
    origins.append("http://localhost:5002")

# Apply CORS to Quart app
app = cors(app,
     allow_origin=origins,
     allow_headers=["Content-Type","Authorization","X-Firebase-AppCheck", "baggage", "sentry-trace"])

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
app.config['MAX_FORM_MEMORY_SIZE'] = 1 * 1024 * 1024  # Only keep 1MB in memory, rest goes to temp file
app.config['BODY_TIMEOUT'] = 3600  # 1 hour timeout for large uploads

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

ffmpeg_queue = Queue('ffmpeg', connection=Redis(), default_timeout=18000) # 5 hours
umbrel_queue = Queue('umbrel', connection=Redis(), default_timeout=18000) # 5 hours

# TODO: set Firebase hosting IP to be static, so I can whitelist it in the backend??? 🤔
def verify_firebase_token(f):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        if IS_DEV:
            # When in development mode, we bypass token verification and mock the user.
            request.user = {'email': 'dev@example.com', 'uid': 'dev-user'}
            return await f(*args, **kwargs)
        
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'No authorization token provided'}), 401

        token = auth_header.split('Bearer ')[1]
        try:
            # Verify the ID token
            decoded_token = auth.verify_id_token(token)
            # Add the user info to the request context
            request.user = decoded_token
            return await f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Token verification failed: {str(e)}")
            return jsonify({'error': 'Invalid authorization token'}), 401        
    return decorated_function

@app.before_request
async def verify_app_check() -> None:
    # no AppCheck for OPTIONS requests (CORS preflight), health endpoint, or dev mode
    if IS_DEV or request.method == 'OPTIONS' or request.path == '/health':
        return
    
    app_check_token = request.headers.get("X-Firebase-AppCheck", default="")
    try:
        app_check.verify_token(app_check_token)
        # If verify_token() succeeds, okay to continue to route handler.
    except (ValueError, jwt.exceptions.DecodeError):
        logger.error(f"App Check token verification failed: {app_check_token}")
        abort(401)
    
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
async def upload_video():
    """
    Quart-native async file upload handler.
    Uses await request.files and await file.read() for true streaming - no buffering!
    Based on: https://stackoverflow.com/questions/59135171/uploading-files-to-a-quart-based-server
    """
    try:
        logger.debug(f"Received upload request from user: {request.user.get('email', 'unknown')}")
        
        # Get form data asynchronously - Quart streams this without buffering
        form = await request.form
        files = await request.files
        
        # Extract form fields
        should_compress = form.get('shouldCompress', 'true').lower() == 'true'
        folder = form.get('folder', '').strip() or None
        
        # Get uploaded file
        file = files.get('file')
        if not file:
            logger.error("No file part in request")
            return jsonify({'error': 'No file part'}), 400
        
        if not file.filename or file.filename == '':
            logger.error("Empty filename")
            return jsonify({'error': 'No selected file'}), 400
        
        logger.debug(f"Received file: {file.filename}, compression: {should_compress}, folder: {folder}")
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type'}), 400
        
        # Secure the filename
        filename = secure_filename(file.filename)
        filename = filename.replace("_", " ")  # undo stupid whitespace -> _ replacement
        target_dir = app.config['UNCOMPRESSED_FOLDER']
        filepath = os.path.join(target_dir, filename)
        
        # Additional security check
        if not is_safe_path(filepath):
            logger.error(f"Invalid file path: {filepath}")
            return jsonify({'error': 'Invalid file path'}), 400
        
        # Create unique filename to prevent overwriting
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(filepath):
            filepath = os.path.join(target_dir, f"{base}_{counter}{ext}")
            counter += 1
        
        logger.debug(f"Streaming file to disk: {filepath}")
        
        # Stream file to disk in chunks using async I/O to prevent blocking the event loop
        # Using 8MB chunks for better performance with large files (per best practices)
        chunk_size = 8 * 1024 * 1024  # 8MB chunks - optimal for large files
        bytes_written = 0
        
        # Use aiofiles for non-blocking async file operations
        async with aiofiles.open(filepath, 'wb') as f:
            while True:
                # Read chunk synchronously from Quart's temp file
                # Note: file.read() is sync but Quart has already streamed to temp disk
                chunk = file.read(chunk_size)
                if not chunk:
                    break
                # Write asynchronously to prevent blocking event loop
                await f.write(chunk)
                bytes_written += len(chunk)
                
                # Log progress every 500MB
                if bytes_written % (500 * 1024 * 1024) < chunk_size:
                    logger.debug(f"Written {bytes_written / (1024*1024):.1f} MB")
        
        logger.debug(f"File saved successfully: {bytes_written / (1024*1024):.1f} MB total")
        
        # Close Quart's temp file handle
        # No need for try/except - if file doesn't exist, we wouldn't have reached here
        file.close()
        logger.debug("Temp file handle closed")
        
        # Enqueue the video processing job only if compression is enabled
        # Store user UID instead of raw Firebase ID token
        # The raw ID token expires in 1 hour, but we can generate fresh ones using the UID
        job_meta = {
            'user_uid': request.user.get('uid', 'unknown'),
        }

        custom_token = auth.create_custom_token(request.user.get('uid'))
        if isinstance(custom_token, bytes):
            custom_token = custom_token.decode("utf-8")

        api_key = os.environ.get("FIREBASE_API_KEY")
        headers = {}
        app_check_token = request.headers.get("X-Firebase-AppCheck")
        if app_check_token:
            headers["X-Firebase-AppCheck"] = app_check_token  # App Check is enforced for this method

        resp = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={api_key}",
            json={"token": custom_token, "returnSecureToken": True},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        tokens = resp.json()  # has idToken, refreshToken, expiresIn

        job_meta["refresh_token"] = tokens["refreshToken"]

        # Include folder information in the metadata for the Umbrel job
        if folder:
            job_meta['X-Folder'] = folder
            logger.debug(f"Including folder in upload: {folder}")
        else:
            logger.debug("No folder specified for upload")

        if should_compress:
            ffmpeg_job = ffmpeg_queue.enqueue(compress_video, args=[filepath])
            # Store user context instead of raw tokens to avoid expiration issues
            umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, depends_on=ffmpeg_job, meta=job_meta)
        else:
            # If compression is disabled, just upload the original file
            umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, args=[filepath], meta=job_meta)
        
        return jsonify({
            'message': 'File uploaded successfully',
            'filename': os.path.basename(filepath)
        }), 200
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        # Clean up file if it exists
        if 'filepath' in locals() and os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.debug(f"Cleaned up file after error: {filepath}")
            except:
                pass
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/api/health')
@verify_firebase_token
async def health_check():
    return jsonify({"status": "ok"}), 200

@app.route('/api/space')
@verify_firebase_token
async def space():
    # check how much disk space is left
    total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
    return jsonify({"total": total, "used": used, "free": free}), 200

@app.route('/api/folders')
@verify_firebase_token
async def get_folders():
    """Get available folders from Umbrel server"""
    try:
        logger.debug("Fetching folders from Umbrel server")
        # Build folders URL from base server URL
        umbrel_base_url = os.environ.get('UMBREL_SERVER_URL', 'http://100.97.35.4:3029')
        
        umbrel_url = umbrel_base_url + '/api/folders'

        # Forward the authorization headers
        headers = {
            'Authorization': request.headers.get('Authorization'),
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

@app.route('/health')
async def docker_health_check():
    """Unauthenticated health check endpoint for Docker"""
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    asyncio.run(serve(app, Config.from_toml("hypercorn.toml")))