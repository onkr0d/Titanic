import asyncio
import tempfile

import requests
from hypercorn.config import Config
from hypercorn.asyncio import serve
from quart import Quart, request, jsonify, g
from quart.wrappers import Request
from quart.formparser import FormDataParser
from quart_cors import cors
from redis import Redis
from rq import Queue
import os
from quart import abort
from werkzeug.exceptions import RequestTimeout
from werkzeug.utils import secure_filename
import logging
from jobs.job import compress_video, upload_video_to_umbrel
import firebase_admin
from firebase_admin import credentials, auth, app_check
from functools import wraps

import jwt
import sentry_sdk
from sentry_sdk.integrations.quart import QuartIntegration

IS_DEV = os.environ.get('IS_DEV', 'false').lower() == 'true'
logging.basicConfig(level=logging.DEBUG if IS_DEV else logging.INFO)
logger = logging.getLogger(__name__)
app = Quart(__name__)
origins = ["https://titanic.ivan.boston"]

def _sentry_traces_sample_rate() -> float:
    sample_rate = os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "").strip()
    if not sample_rate:
        return 1.0
    try:
        return float(sample_rate)
    except ValueError:
        logger.warning("Invalid SENTRY_TRACES_SAMPLE_RATE=%s; defaulting to 1.0", sample_rate)
        return 1.0

_sentry_dsn = os.environ.get("SENTRY_DSN", "").strip()
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT"),
        integrations=[QuartIntegration()],
        send_default_pii=True,
        traces_sample_rate=_sentry_traces_sample_rate(),
    )

logger.info(f"IS_DEV: {IS_DEV}")

if IS_DEV:
    origins.append("http://localhost:5173")
    origins.append("http://localhost:5174")
    origins.append("http://localhost:6969")
    origins.append("http://localhost:5002")

# Apply CORS to Quart app
app = cors(app,
     allow_origin=origins,
     allow_methods=["GET", "POST", "OPTIONS"],
     allow_headers=["Content-Type","Authorization","X-Firebase-AppCheck", "baggage", "sentry-trace"])

# Initialize Firebase Admin if credentials are available.
# In dev/CI without credentials we warn and continue; in production we fail hard.
cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin-sdk-cred.json'))
if os.path.exists(cred_path):
    cred = credentials.Certificate(cred_path)

    # Prevent Firebase libraries from spamming debug logs
    logging.getLogger("cachecontrol").setLevel(logging.WARNING)
    logging.getLogger("cachecontrol.controller").setLevel(logging.WARNING)
    logging.getLogger("google.auth").setLevel(logging.WARNING)
    logging.getLogger("google.auth.transport").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin SDK initialized")
elif IS_DEV:
    logger.warning("Firebase credentials not found at %s — running without Firebase (dev/CI mode)", cred_path)
else:
    raise FileNotFoundError(f"Firebase credentials file not found: {cred_path}")

# Configure upload settings
UPLOAD_FOLDER = os.path.abspath('videos')  # Base directory for videos
UNCOMPRESSED_FOLDER = os.path.join(UPLOAD_FOLDER, 'uncompressed')
COMPRESSED_FOLDER = os.path.join(UPLOAD_FOLDER, 'compressed')
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv', 'm4v', 'avi', 'webm', 'ts'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['UNCOMPRESSED_FOLDER'] = UNCOMPRESSED_FOLDER
app.config['COMPRESSED_FOLDER'] = COMPRESSED_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024 * 1024  # 20GB max file size
app.config['MAX_FORM_MEMORY_SIZE'] = 1 * 1024 * 1024  # Only keep 1MB in memory, rest goes to temp file
app.config['BODY_TIMEOUT'] = 6 * 3600  # 6 hours — accommodates 20GB on slow residential uplinks

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

def _remove_quietly(path):
    try:
        os.remove(path)
        logger.debug(f"Cleaned up upload artifact: {path}")
    except FileNotFoundError:
        pass
    except OSError as e:
        # Permissions, busy handles, etc. — surface so disk leaks don't go unnoticed.
        logger.warning(f"Failed to remove upload artifact {path}: {e}")


def _cleanup_upload_artifacts():
    """Remove any .part files this request created plus the final filepath if set.

    Pulled from `g` so that uploads which fail *before* the handler captures the temp
    path (e.g. RequestTimeout inside `await request.form`) still get cleaned up.
    """
    for path in getattr(g, '_upload_parts', ()):
        _remove_quietly(path)
    g._upload_parts = []
    final_path = getattr(g, '_upload_final_path', None)
    if final_path:
        _remove_quietly(final_path)
        g._upload_final_path = None


def _upload_stream_factory(total_content_length, content_type, filename, content_length=None):
    # Write the upload directly to UNCOMPRESSED_FOLDER so the post-parse step is an
    # O(1) os.replace rather than a 20GB copy across filesystems.
    f = tempfile.NamedTemporaryFile(
        dir=UNCOMPRESSED_FOLDER, delete=False, prefix='upload_', suffix='.part'
    )
    # Register the path in request-local state so cleanup can find it even if
    # parsing aborts before the handler captures `file.stream.name`.
    parts = getattr(g, '_upload_parts', None)
    if parts is None:
        parts = []
        g._upload_parts = parts
    parts.append(f.name)
    return f


class _StreamingRequest(Request):
    def make_form_data_parser(self) -> FormDataParser:
        return self.form_data_parser_class(
            max_content_length=self.max_content_length,
            max_form_memory_size=self.max_form_memory_size,
            max_form_parts=self.max_form_parts,
            cls=self.parameter_storage_class,
            stream_factory=_upload_stream_factory,
        )


app.request_class = _StreamingRequest


@app.teardown_request
async def _teardown_upload_cleanup(_exc):
    # Safety net: any .part files registered by _upload_stream_factory that the handler
    # didn't consume (early return, parse-time exception, etc.) get removed here.
    if getattr(g, '_upload_parts', None) or getattr(g, '_upload_final_path', None):
        _cleanup_upload_artifacts()

ffmpeg_queue = Queue('ffmpeg', connection=Redis(), default_timeout=19800) # 5.5 hours
umbrel_queue = Queue('umbrel', connection=Redis(), default_timeout=7200) # 2 hours

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
    The body is streamed directly to UNCOMPRESSED_FOLDER by _upload_stream_factory,
    so the only post-parse work is an os.replace to the final filename.
    """
    try:
        logger.debug(f"Received upload request from user: {request.user.get('email', 'unknown')}")

        # Get form data asynchronously - Quart streams the file body straight to disk
        # via _upload_stream_factory, so no extra buffering happens here.
        # NOTE: 6h BODY_TIMEOUT means a single slow upload occupies a Hypercorn worker
        # for that long. Fine at current traffic; revisit if upload concurrency grows.
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

        # `file.stream` is the NamedTemporaryFile returned by _upload_stream_factory,
        # so `.name` is the on-disk .part path we registered in `g._upload_parts`.
        part_path = file.stream.name

        logger.debug(f"Received file: {file.filename}, compression: {should_compress}, folder: {folder}")

        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type'}), 400

        # Secure the filename
        filename = secure_filename(file.filename)
        filename = filename.replace("_", " ")  # undo stupid whitespace -> _ replacement
        target_dir = app.config['UNCOMPRESSED_FOLDER']
        base, ext = os.path.splitext(filename)
        candidate = os.path.join(target_dir, filename)

        # Additional security check
        if not is_safe_path(candidate):
            logger.error(f"Invalid file path: {candidate}")
            return jsonify({'error': 'Invalid file path'}), 400

        # Atomically claim a unique filename via O_EXCL — avoids the TOCTOU race where
        # two concurrent uploads with the same filename pick the same target.
        counter = 0
        while True:
            try:
                # No mode arg: os.replace below swaps in the .part file's inode
                # (NamedTemporaryFile default 0o600), so the placeholder mode is moot.
                fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                counter += 1
                candidate = os.path.join(target_dir, f"{base}_{counter}{ext}")
                if not is_safe_path(candidate):
                    return jsonify({'error': 'Invalid file path'}), 400

        filepath = candidate
        g._upload_final_path = filepath

        # Close the temp file handle, then rename it into place — same fs, O(1).
        # os.replace clobbers the 0-byte placeholder we just created with O_EXCL.
        file.close()
        os.replace(part_path, filepath)
        # Drop the renamed .part from the tracked list so cleanup won't try again.
        g._upload_parts = [p for p in getattr(g, '_upload_parts', ()) if p != part_path]
        logger.debug(f"File saved: {filepath}")
        
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
            ffmpeg_job = ffmpeg_queue.enqueue(compress_video, args=[filepath], result_ttl=86400)
            # Store user context instead of raw tokens to avoid expiration issues
            umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, depends_on=ffmpeg_job, meta=job_meta)
        else:
            # If compression is disabled, just upload the original file
            umbrel_job = umbrel_queue.enqueue(upload_video_to_umbrel, args=[filepath], meta=job_meta)
        
        # Job successfully enqueued — handler owns the file no longer; don't clean up.
        g._upload_final_path = None
        return jsonify({
            'message': 'File uploaded successfully',
            'filename': os.path.basename(filepath)
        }), 200

    except RequestTimeout:
        # Body didn't arrive within BODY_TIMEOUT — surface 408 instead of swallowing
        # it into a generic 500 so the frontend can show a meaningful message.
        logger.warning("Upload aborted: client did not finish body within BODY_TIMEOUT")
        _cleanup_upload_artifacts()
        return jsonify({'error': 'Upload timed out — connection too slow to finish within the server window'}), 408
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        _cleanup_upload_artifacts()
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/api/health')
@verify_firebase_token
async def health_check():
    return jsonify({"status": "ok"}), 200

@app.route('/api/space')
@verify_firebase_token
async def space():
    # Forward to Umbrel server to get actual disk space of the mounted volume
    try:
        logger.debug("Fetching disk space from Umbrel server")
        umbrel_base_url = os.environ.get('UMBREL_SERVER_URL', 'http://umbrel:3029')
        umbrel_url = umbrel_base_url + '/api/space'

        # Forward the authorization headers
        headers = {
            'Authorization': request.headers.get('Authorization'),
        }

        response = requests.get(umbrel_url, headers=headers, timeout=10)
        response.raise_for_status()

        space_data = response.json()
        return jsonify(space_data), 200

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching space from Umbrel: {str(e)}")
        return jsonify({'error': f'Failed to fetch space from Umbrel: {str(e)}'}), 502
    except Exception as e:
        logger.error(f"Unexpected error fetching space: {str(e)}")
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/api/config')
@verify_firebase_token
async def get_config():
    """Get app configuration derived from Umbrel settings"""
    try:
        logger.debug("Fetching settings from Umbrel server for config extraction")
        umbrel_base_url = os.environ.get('UMBREL_SERVER_URL', 'http://umbrel:3029')
        umbrel_url = umbrel_base_url + '/api/settings'

        # Forward the authorization headers
        headers = {
            'Authorization': request.headers.get('Authorization'),
        }

        response = requests.get(umbrel_url, headers=headers, timeout=10)
        response.raise_for_status()

        settings_data = response.json()
        return jsonify({
            "default_folder": settings_data.get("default_folder")
        }), 200

    except Exception as e:
        logger.error(f"Error fetching config from Umbrel settings: {str(e)}")
        # If settings are inaccessible, just return empty config (it will default to 'Clips' in frontend)
        return jsonify({"default_folder": None}), 200

@app.route('/api/folders')
@verify_firebase_token
async def get_folders():
    """Get available folders from Umbrel server"""
    try:
        logger.debug("Fetching folders from Umbrel server")
        # Build folders URL from base server URL
        umbrel_base_url = os.environ.get('UMBREL_SERVER_URL', 'http://umbrel:3029')
        
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