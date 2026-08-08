"""One Firestore document per transcode job to measure the H.264 -> H.265 savings ratio.

Best-effort throughout: recording must never break an upload. Silently disabled
when Firebase isn't initialized, which is the normal dev/CI case.
"""

import functools
import hashlib
import logging
import os
import statistics
from datetime import UTC, datetime

import firebase_admin

logger = logging.getLogger(__name__)

COLLECTION = "transcode_jobs"

# Fire-and-forget bookkeeping: give up rather than hold a worker on the network.
WRITE_TIMEOUT_SECONDS = 5.0

STATUS_TRANSCODED = "transcoded"
STATUS_SKIPPED_ALREADY_HEVC = "skipped_already_hevc"
STATUS_SHAREABLE_ONLY = "shareable_only"
STATUS_FAILED = "failed"


def _never_raises(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.exception("Metrics call %s failed; continuing", func.__name__)
            return None

    return wrapper


def _ensure_app():
    """The Firebase app, initializing it if this process hasn't.

    `get_app()` raises when never initialized — the dev/CI case, but also every
    RQ worker, which is a separate process from the app that initializes Firebase
    and does not initialize it itself.
    """
    try:
        return firebase_admin.get_app()
    except ValueError:
        from jobs.job import initialize_firebase

        initialize_firebase()
        return firebase_admin.get_app()


@functools.cache
def _collection():
    """The metrics collection, or None when Firebase isn't available.

    Cached because the answer is a property of the process, not of the job, and
    because it decides whether to pay for a Firebase init.
    """
    try:
        _ensure_app()

        # Imported only once an app exists: google-cloud-firestore is a ~4s
        # import, wasted in processes that will never write a metric.
        from firebase_admin import firestore

        return firestore.client().collection(COLLECTION)
    except Exception:
        # Once per process, and quietly in dev, where no credentials is normal.
        log = logger.debug if _is_dev() else logger.warning
        log("Firestore unavailable; transcode metrics disabled", exc_info=True)
        return None


def _is_dev():
    return os.environ.get("IS_DEV", "false").lower() == "true"


def _doc_id(input_file: str) -> str:
    """Keyed on the upload's identity, not just its name.

    Uploads are uniquified only while the previous file exists, and the pipeline
    deletes it — so a later upload can reuse a freed name and would otherwise
    overwrite that job's row. Size and mtime separate them, while a re-enqueue of
    the same file on disk still dedupes onto one document.
    """
    key = os.fsencode(input_file)
    try:
        stat = os.stat(input_file)
        key += b"|%d|%d" % (stat.st_size, stat.st_mtime_ns)
    except OSError:
        pass
    return hashlib.sha256(key).hexdigest()


def _size_or_none(path):
    if not path:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def load_documents() -> list[dict]:
    """Every recorded job. Empty when Firestore isn't reachable."""
    collection = _collection()
    return [d.to_dict() for d in collection.stream()] if collection else []


def aggregate(documents) -> dict:
    """Fold recorded jobs into the numbers worth quoting.

    Only `transcoded` jobs reach the ratio — a size-targeted copy was compressed
    to a byte budget, a skipped job never re-encoded, a failed one produced
    nothing. Easy to forget when eyeballing the console, which is why it lives
    here. The result is whole-pipeline (rnnoise and loudnorm run before the
    encode), so quote it as such.

        python -c "import jobs.metrics as m; print(m.aggregate(m.load_documents()))"
    """
    total = transcoded = source_bytes = output_bytes = 0
    seconds = 0.0
    ratios = []

    for doc in documents:
        total += 1

        duration = doc.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration > 0:
            seconds += duration

        source, output = doc.get("source_bytes"), doc.get("full_output_bytes")
        if (
            doc.get("status") != STATUS_TRANSCODED
            or not isinstance(source, (int, float))
            or not isinstance(output, (int, float))
            or source <= 0
        ):
            continue

        transcoded += 1
        source_bytes += source
        output_bytes += output
        ratios.append(output / source)

    saved = source_bytes - output_bytes
    return {
        "jobs_total": total,
        "jobs_transcoded": transcoded,
        "source_bytes_total": source_bytes,
        "output_bytes_total": output_bytes,
        "bytes_saved": saved,
        "savings_ratio": round(saved / source_bytes, 4) if source_bytes else 0.0,
        "median_transcode_ratio": round(statistics.median(ratios), 4) if ratios else 0.0,
        "hours_of_video": round(seconds / 3600, 2),
    }


class JobRecord:
    """One job's metrics, written once at the end.

    Source-side fields are read at construction because the pipeline deletes the
    original before it finishes.
    """

    def __init__(self, input_file: str, probe=None, target_size_mb=None):
        self.doc_id = _doc_id(input_file)
        self.source_codec = None
        self._fields = {}
        self._capture_source(input_file, probe, target_size_mb)

    @_never_raises
    def _capture_source(self, input_file, probe, target_size_mb):
        self._fields = {
            "created_at": datetime.now(UTC),
            "source_bytes": _size_or_none(input_file),
            "target_size_mb": target_size_mb,
        }
        # Recorded generously: unlike the ratio, these cost one probe and are
        # unrecoverable after the pipeline deletes the original.
        stream = next(
            (s for s in (probe or {}).get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        codec = stream.get("codec_name")
        self.source_codec = codec.lower() if codec else None
        self._fields.update(
            {
                "source_codec": self.source_codec,
                "width": stream.get("width"),
                "height": stream.get("height"),
                "duration_seconds": _float_or_none((probe or {}).get("format", {}).get("duration")),
            }
        )

    @_never_raises
    def _write(self, fields):
        collection = _collection()
        if collection is None:
            return
        # merge so a re-processed upload updates its document rather than
        # blanking fields the earlier run had set.
        collection.document(self.doc_id).set(
            {**self._fields, **fields}, merge=True, timeout=WRITE_TIMEOUT_SECONDS
        )

    @_never_raises
    def finish(
        self,
        status,
        full_output=None,
        shareable_output=None,
        transcode_seconds=None,
        encode_params=None,
    ):
        self._write(
            {
                "status": status,
                "full_output_bytes": _size_or_none(full_output),
                "shareable_output_bytes": _size_or_none(shareable_output),
                "transcode_seconds": transcode_seconds,
                "encode_params": encode_params,
            }
        )

    @_never_raises
    def fail(self, exc):
        # Class name only. Exception messages embed the upload path, and so the
        # user's filename — CalledProcessError stringifies the whole argv. Sentry
        # holds the detail; this keeps the collection free of identifying data.
        self._write({"status": STATUS_FAILED, "failure_reason": type(exc).__name__})


def _float_or_none(value):
    """ffprobe reports 'N/A' on containers that don't carry a duration."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
