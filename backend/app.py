import asyncio
import logging
import os
import shutil
import tempfile
from functools import wraps

import firebase_admin
import jwt
import requests
import sentry_sdk
from firebase_admin import app_check, auth, credentials
from hypercorn.asyncio import serve
from hypercorn.config import Config
from quart import Quart, abort, g, jsonify, request
from quart.formparser import FormDataParser
from quart.wrappers import Request
from quart_cors import cors
from redis import Redis
from rq import Queue
from sentry_sdk.integrations.quart import QuartIntegration
from werkzeug.exceptions import RequestTimeout

from fileutils import remove_quietly, sanitize_path_component, scrub_event
from jobs.job import compress_video, upload_video_to_umbrel
from jobs.shareable import (
    SHAREABLE_AUDIO_KBPS,
    SHAREABLE_MAX_TARGET_MB,
    SHAREABLE_MIN_VIDEO_KBPS,
    SHAREABLE_SIZE_MARGIN,
)

IS_DEV = os.environ.get("IS_DEV", "false").lower() == "true"
# Disabling authentication is deliberately its OWN flag, not a side effect of
# IS_DEV. IS_DEV toggles convenience only (debug logs, localhost CORS); flipping
# it in a prod image must never silently drop auth. The bypass also requires
# IS_DEV so it can't be turned on by itself in a production environment.
DEV_AUTH_BYPASS = (
    IS_DEV and os.environ.get("DEV_AUTH_BYPASS", "false").lower() == "true"
)
logging.basicConfig(level=logging.DEBUG if IS_DEV else logging.INFO)
logger = logging.getLogger(__name__)
if DEV_AUTH_BYPASS:
    logger.warning(
        "DEV_AUTH_BYPASS is ON — Firebase token and App Check verification are "
        "DISABLED. This must never be set in production."
    )
app = Quart(__name__)
origins = ["https://titanic.ivan.boston"]


def _sentry_traces_sample_rate() -> float:
    sample_rate = os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "").strip()
    if not sample_rate:
        return 1.0
    try:
        return float(sample_rate)
    except ValueError:
        logger.warning(
            "Invalid SENTRY_TRACES_SAMPLE_RATE=%s; defaulting to 1.0", sample_rate
        )
        return 1.0


_sentry_dsn = os.environ.get("SENTRY_DSN", "").strip()
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT"),
        integrations=[QuartIntegration()],
        send_default_pii=True,
        before_send=scrub_event,
        traces_sample_rate=_sentry_traces_sample_rate(),
    )

logger.info(f"IS_DEV: {IS_DEV}")

if IS_DEV:
    origins.append("http://localhost:5173")
    origins.append("http://localhost:5174")
    origins.append("http://localhost:6969")
    origins.append("http://localhost:5002")

# Apply CORS to Quart app
app = cors(
    app,
    allow_origin=origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Firebase-AppCheck",
        "baggage",
        "sentry-trace",
    ],
)

# Initialize Firebase Admin if credentials are available.
# In dev/CI without credentials we warn and continue; in production we fail hard.
cred_path = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin-sdk-cred.json"),
)
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
    logger.warning(
        "Firebase credentials not found at %s — running without Firebase (dev/CI mode)",
        cred_path,
    )
else:
    raise FileNotFoundError(f"Firebase credentials file not found: {cred_path}")

# Configure upload settings
UPLOAD_FOLDER = os.path.abspath("videos")  # Base directory for videos
UNCOMPRESSED_FOLDER = os.path.join(UPLOAD_FOLDER, "uncompressed")
COMPRESSED_FOLDER = os.path.join(UPLOAD_FOLDER, "compressed")
ALLOWED_EXTENSIONS = {
    "mp4",
    "avi",
    "mov",
    "mkv",
    "wmv",
    "flv",
    "m4v",
    "avi",
    "webm",
    "ts",
}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["UNCOMPRESSED_FOLDER"] = UNCOMPRESSED_FOLDER
app.config["COMPRESSED_FOLDER"] = COMPRESSED_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 42 * 1024 * 1024 * 1024  # 42GB max file size
# Headroom required beyond the declared upload size before we accept a stream to
# disk. The compressed copy and other in-flight artifacts share this volume, so we
# keep a buffer rather than letting an upload fill it to the last byte.
UPLOAD_DISK_SAFETY_MARGIN = 2 * 1024 * 1024 * 1024  # 2GB
app.config["MAX_FORM_MEMORY_SIZE"] = (
    1 * 1024 * 1024
)  # Only keep 1MB in memory, rest goes to temp file
app.config["BODY_TIMEOUT"] = (
    6 * 3600
)  # 6 hours — accommodates 20GB on slow residential uplinks

# Ensure upload directories exist
try:
    os.makedirs(UNCOMPRESSED_FOLDER, exist_ok=True)
    os.makedirs(COMPRESSED_FOLDER, exist_ok=True)
except PermissionError as e:
    logger.warning(
        f"Could not create upload directories: {e}. They may already exist with correct permissions."
    )
    # Check if directories exist and are writable
    if not os.path.exists(UNCOMPRESSED_FOLDER) or not os.access(
        UNCOMPRESSED_FOLDER, os.W_OK
    ):
        logger.error(f"Upload directory {UNCOMPRESSED_FOLDER} is not accessible")
        raise
    if not os.path.exists(COMPRESSED_FOLDER) or not os.access(
        COMPRESSED_FOLDER, os.W_OK
    ):
        logger.error(f"Compressed directory {COMPRESSED_FOLDER} is not accessible")
        raise


def _cleanup_upload_artifacts():
    """Remove any .part files this request created plus the final filepath if set.

    Pulled from `g` so that uploads which fail *before* the handler captures the temp
    path (e.g. RequestTimeout inside `await request.form`) still get cleaned up.
    """
    for path in getattr(g, "_upload_parts", ()):
        remove_quietly(path)
    g._upload_parts = []
    final_path = getattr(g, "_upload_final_path", None)
    if final_path:
        remove_quietly(final_path)
        g._upload_final_path = None


def _upload_stream_factory(
    total_content_length, content_type, filename, content_length=None
):
    # Write the upload directly to UNCOMPRESSED_FOLDER so the post-parse step is an
    # O(1) os.replace rather than a 20GB copy across filesystems.
    f = tempfile.NamedTemporaryFile(
        dir=UNCOMPRESSED_FOLDER, delete=False, prefix="upload_", suffix=".part"
    )
    # Register the path in request-local state so cleanup can find it even if
    # parsing aborts before the handler captures `file.stream.name`.
    parts = getattr(g, "_upload_parts", None)
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
    if getattr(g, "_upload_parts", None) or getattr(g, "_upload_final_path", None):
        _cleanup_upload_artifacts()


_redis = Redis(password=os.environ.get("REDIS_PASSWORD") or None)  # matches start.sh --requirepass
ffmpeg_queue = Queue(
    "ffmpeg", connection=_redis, default_timeout=86400
)  # 24 hours, probably really bad :(
umbrel_queue = Queue("umbrel", connection=_redis, default_timeout=7200)  # 2 hours


# TODO: set Firebase hosting IP to be static, so I can whitelist it in the backend??? 🤔
def verify_firebase_token(f):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        if DEV_AUTH_BYPASS:
            # Local-only escape hatch: bypass token verification and mock the user.
            request.user = {"email": "dev@example.com", "uid": "dev-user"}
            return await f(*args, **kwargs)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "No authorization token provided"}), 401

        token = auth_header.split("Bearer ")[1]
        try:
            # Verify the ID token
            decoded_token = auth.verify_id_token(token)
            # Add the user info to the request context
            request.user = decoded_token
            return await f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Token verification failed: {str(e)}")
            return jsonify({"error": "Invalid authorization token"}), 401

    return decorated_function


@app.before_request
async def verify_app_check() -> None:
    # no AppCheck for OPTIONS requests (CORS preflight), health endpoint, or when
    # the local dev auth bypass is explicitly enabled
    if DEV_AUTH_BYPASS or request.method == "OPTIONS" or request.path == "/health":
        return

    app_check_token = request.headers.get("X-Firebase-AppCheck", default="")
    try:
        app_check.verify_token(app_check_token)
        # If verify_token() succeeds, okay to continue to route handler.
    except (ValueError, jwt.exceptions.DecodeError):
        abort(401)


def allowed_file(filename):
    # Check for null bytes
    if "\0" in filename:
        return False
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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


class UploadError(Exception):
    """Abort an upload with a specific HTTP status.

    Raised by the upload helpers; caught in upload_video, which runs artifact
    cleanup and returns `message` as JSON with `status_code`.
    """

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _ensure_disk_space(content_length):
    """Reject up front if the volume can't hold this upload plus a safety margin.

    Avoids streaming gigabytes to disk only to hit ENOSPC mid-parse. content_length
    is the full multipart body (a slight over-estimate of the file itself), which
    errs in the conservative direction. A missing Content-Length (e.g. chunked
    transfer-encoding) can't be checked, so we let it through.
    """
    if not content_length:
        return
    try:
        free_bytes = shutil.disk_usage(UNCOMPRESSED_FOLDER).free
    except OSError as e:
        logger.warning(f"Could not check free disk space, proceeding anyway: {e}")
        return
    needed = content_length + UPLOAD_DISK_SAFETY_MARGIN
    if free_bytes < needed:
        logger.warning(
            f"Rejecting upload: need {needed} bytes "
            f"(upload {content_length} + {UPLOAD_DISK_SAFETY_MARGIN} margin), "
            f"only {free_bytes} free on {UNCOMPRESSED_FOLDER}"
        )
        raise UploadError(
            507, "Not enough free disk space on the server to accept this upload"
        )


def _claim_upload_path(file):
    """Validate the uploaded file and move its streamed .part into a final path.

    Returns the on-disk path. Raises UploadError(400) on a bad name/type or unsafe
    path. A unique name is claimed atomically via O_EXCL to avoid the TOCTOU race
    between concurrent uploads of the same filename.
    """
    # `file.stream` is the NamedTemporaryFile returned by _upload_stream_factory,
    # so `.name` is the on-disk .part path we registered in `g._upload_parts`.
    part_path = file.stream.name

    if not allowed_file(file.filename):
        raise UploadError(400, "Invalid file type")

    # Sanitize into a single safe path component (mirrors umbrel) — preserves
    # '?' and non-ASCII so names aren't silently mangled. is_safe_path below is
    # the traversal backstop.
    filename = sanitize_path_component(file.filename)
    if filename is None:
        raise UploadError(400, "Invalid filename")
    target_dir = app.config["UNCOMPRESSED_FOLDER"]
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(target_dir, filename)

    if not is_safe_path(candidate):
        logger.error(f"Invalid file path: {candidate}")
        raise UploadError(400, "Invalid file path")

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
                raise UploadError(400, "Invalid file path")

    filepath = candidate
    g._upload_final_path = filepath

    # Close the temp file handle, then rename it into place — same fs, O(1).
    # os.replace clobbers the 0-byte placeholder we just created with O_EXCL.
    file.close()
    os.replace(part_path, filepath)
    # Drop the renamed .part from the tracked list so cleanup won't try again.
    g._upload_parts = [p for p in getattr(g, "_upload_parts", ()) if p != part_path]
    logger.debug(f"File saved: {filepath}")
    return filepath


def _mint_refresh_token(user):
    """Exchange the authenticated user's UID for a Firebase refresh token.

    The job stores a refresh token rather than the raw ID token (which expires in
    1 hour) so the worker can mint fresh ID tokens when it runs. Network call;
    raises on failure.
    """
    custom_token = auth.create_custom_token(user.get("uid"))
    if isinstance(custom_token, bytes):
        custom_token = custom_token.decode("utf-8")

    api_key = os.environ.get("FIREBASE_API_KEY")
    headers = {}
    app_check_token = request.headers.get("X-Firebase-AppCheck")
    if app_check_token:
        headers["X-Firebase-AppCheck"] = app_check_token  # App Check is enforced here

    resp = requests.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={api_key}",
        json={"token": custom_token, "returnSecureToken": True},
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["refreshToken"]  # response also has idToken, expiresIn


def _enqueue_processing(
    filepath, job_meta, should_compress, target_size_mb=None, keep_full_quality=True
):
    """Enqueue the upload-to-Umbrel job, optionally behind an ffmpeg compress job.

    The enqueue pair is the commit point. If the umbrel enqueue fails after the
    ffmpeg job is already queued, best-effort cancel the ffmpeg job so it can't
    later run against a file the caller's error path is about to delete (the race
    flagged in PR #235's review).

    A target_size_mb adds a size-capped copy (skipped if the output already fits);
    keep_full_quality=False delivers only that copy, under the original filename.
    """
    if not should_compress:
        # If compression is disabled, just upload the original file
        umbrel_queue.enqueue(upload_video_to_umbrel, args=[filepath], meta=job_meta)
        return

    ffmpeg_job = ffmpeg_queue.enqueue(
        compress_video,
        args=[filepath, target_size_mb, keep_full_quality],
        result_ttl=86400,
    )
    try:
        umbrel_queue.enqueue(
            upload_video_to_umbrel, depends_on=ffmpeg_job, meta=job_meta
        )
    except Exception:
        # Best-effort: a worker may already have picked up the ffmpeg job, so this
        # isn't atomic — but it closes the common case (Redis blip between the two
        # enqueues) before we re-raise into the caller's cleanup.
        try:
            ffmpeg_job.cancel()
        except Exception as cancel_err:
            logger.warning(
                f"Failed to cancel ffmpeg job after umbrel enqueue failure: {cancel_err}"
            )
        raise


@app.route("/api/upload", methods=["POST"])
@verify_firebase_token
async def upload_video():
    """
    Quart-native async file upload handler.
    The body is streamed directly to UNCOMPRESSED_FOLDER by _upload_stream_factory,
    so the only post-parse work is an os.replace to the final filename.
    """
    try:
        logger.debug(
            f"Received upload request from user: {request.user.get('email', 'unknown')}"
        )

        # Fail fast & cheap before streaming gigabytes to disk.
        _ensure_disk_space(request.content_length)

        # Get form data asynchronously - Quart streams the file body straight to disk
        # via _upload_stream_factory, so no extra buffering happens here.
        # NOTE: 6h BODY_TIMEOUT means a single slow upload occupies a Hypercorn worker
        # for that long. Fine at current traffic; revisit if upload concurrency grows.
        form = await request.form
        files = await request.files

        should_compress = form.get("shouldCompress", "true").lower() == "true"

        # Optional target size (MB) for an extra Discord-shareable copy. Size-targeting
        # implies a re-encode, so it forces compression on regardless of the toggle.
        target_size_mb = None
        target_raw = form.get("targetSizeMb", "").strip()
        if target_raw:
            try:
                target_size_mb = float(target_raw)
            except ValueError:
                raise UploadError(400, "Invalid target size")
            if not (0 < target_size_mb <= SHAREABLE_MAX_TARGET_MB):
                raise UploadError(
                    400,
                    f"Target size must be between 0 and {SHAREABLE_MAX_TARGET_MB} MB",
                )
            should_compress = True

        # Deliver only the size-capped copy; meaningless without a target.
        keep_full_quality = form.get("keepFullQuality", "true").lower() != "false"
        if not keep_full_quality and target_size_mb is None:
            raise UploadError(400, "keepFullQuality=false requires targetSizeMb")

        folder = form.get("folder", "").strip() or None
        # Fail fast: reject a bad folder name here (before the expensive compress
        # job) rather than letting it fail at the umbrel step post-processing.
        # umbrel re-validates with the same rules as the authoritative check.
        if folder is not None:
            folder = sanitize_path_component(folder)
            if folder is None:
                raise UploadError(400, "Invalid folder name")

        file = files.get("file")
        if not file:
            raise UploadError(400, "No file part")
        if not file.filename:
            raise UploadError(400, "No selected file")

        logger.debug(
            f"Received file: {file.filename}, compression: {should_compress}, folder: {folder}"
        )

        # Do all fallible work (the Firebase token exchange) BEFORE committing the
        # file to its final path. If it fails here, the upload is still just a
        # tracked .part that teardown cleans up — nothing committed is stranded.
        # Store the user's UID plus a refresh token (not the 1h ID token) so the
        # worker can mint fresh credentials when it runs.
        job_meta = {"user_uid": request.user.get("uid", "unknown")}
        job_meta["refresh_token"] = _mint_refresh_token(request.user)
        if folder:
            job_meta["X-Folder"] = folder
            logger.debug(f"Including folder in upload: {folder}")

        # Commit point: rename the .part into place, then enqueue the job pair.
        filepath = _claim_upload_path(file)
        _enqueue_processing(
            filepath, job_meta, should_compress, target_size_mb, keep_full_quality
        )

        # Job successfully enqueued — handler owns the file no longer; don't clean up.
        g._upload_final_path = None
        return jsonify(
            {
                "message": "File uploaded successfully",
                "filename": os.path.basename(filepath),
            }
        ), 200

    except UploadError as e:
        _cleanup_upload_artifacts()
        return jsonify({"error": e.message}), e.status_code
    except RequestTimeout:
        # Body didn't arrive within BODY_TIMEOUT — surface 408 instead of swallowing
        # it into a generic 500 so the frontend can show a meaningful message.
        logger.warning("Upload aborted: client did not finish body within BODY_TIMEOUT")
        _cleanup_upload_artifacts()
        return jsonify(
            {
                "error": "Upload timed out — connection too slow to finish within the server window"
            }
        ), 408
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        _cleanup_upload_artifacts()
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


@app.route("/api/health")
@verify_firebase_token
async def health_check():
    return jsonify({"status": "ok"}), 200


@app.route("/api/space")
@verify_firebase_token
async def space():
    # Forward to Umbrel server to get actual disk space of the mounted volume
    try:
        logger.debug("Fetching disk space from Umbrel server")
        umbrel_base_url = os.environ.get("UMBREL_SERVER_URL", "http://umbrel:3029")
        umbrel_url = umbrel_base_url + "/api/space"

        # Forward the authorization headers
        headers = {
            "Authorization": request.headers.get("Authorization"),
        }

        response = requests.get(umbrel_url, headers=headers, timeout=10)
        response.raise_for_status()

        space_data = response.json()
        return jsonify(space_data), 200

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching space from Umbrel: {str(e)}")
        return jsonify({"error": f"Failed to fetch space from Umbrel: {str(e)}"}), 502
    except Exception as e:
        logger.error(f"Unexpected error fetching space: {str(e)}")
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


@app.route("/api/config")
@verify_firebase_token
async def get_config():
    """Get app configuration derived from Umbrel settings"""
    # Encode-budget constants for the frontend's shareable-copy quality prediction;
    # served here so the two sides can't drift.
    shareable_config = {
        "audio_kbps": SHAREABLE_AUDIO_KBPS,
        "size_margin": SHAREABLE_SIZE_MARGIN,
        "min_video_kbps": SHAREABLE_MIN_VIDEO_KBPS,
        "max_target_mb": SHAREABLE_MAX_TARGET_MB,
        # Capability flags so a newer frontend can gate UI on backend support.
        "skip_if_under": True,
        "supports_only": True,
    }
    try:
        logger.debug("Fetching settings from Umbrel server for config extraction")
        umbrel_base_url = os.environ.get("UMBREL_SERVER_URL", "http://umbrel:3029")
        umbrel_url = umbrel_base_url + "/api/settings"

        # Forward the authorization headers
        headers = {
            "Authorization": request.headers.get("Authorization"),
        }

        response = requests.get(umbrel_url, headers=headers, timeout=10)
        response.raise_for_status()

        settings_data = response.json()
        return jsonify(
            {
                "default_folder": settings_data.get("default_folder"),
                "shareable": shareable_config,
            }
        ), 200

    except Exception as e:
        logger.error(f"Error fetching config from Umbrel settings: {str(e)}")
        # If settings are inaccessible, just return empty config (it will default to 'Clips' in frontend)
        return jsonify({"default_folder": None, "shareable": shareable_config}), 200


@app.route("/api/folders")
@verify_firebase_token
async def get_folders():
    """Get available folders from Umbrel server"""
    try:
        logger.debug("Fetching folders from Umbrel server")
        # Build folders URL from base server URL
        umbrel_base_url = os.environ.get("UMBREL_SERVER_URL", "http://umbrel:3029")

        umbrel_url = umbrel_base_url + "/api/folders"

        # Forward the authorization headers
        headers = {
            "Authorization": request.headers.get("Authorization"),
        }

        response = requests.get(umbrel_url, headers=headers, timeout=30)
        response.raise_for_status()

        folders_data = response.json()
        logger.debug(f"Retrieved folders: {folders_data}")

        return jsonify(folders_data), 200

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching folders from Umbrel: {str(e)}")
        return jsonify({"error": f"Failed to fetch folders: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"Unexpected error fetching folders: {str(e)}")
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


@app.route("/health")
async def docker_health_check():
    """Unauthenticated health check endpoint for Docker"""
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    hypercorn_config = Config.from_toml("hypercorn.toml")
    # Dev/test run behind a Docker bridge with a published port, which forwards to
    # the container's external interface — so they set HYPERCORN_BIND=0.0.0.0:5000.
    # Prod leaves this unset and keeps the loopback bind from the toml.
    bind_override = os.environ.get("HYPERCORN_BIND")
    if bind_override:
        hypercorn_config.bind = [bind_override]
        hypercorn_config.quic_bind = [bind_override]
    asyncio.run(serve(app, hypercorn_config))
