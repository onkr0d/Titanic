import logging
import os
import unicodedata

logger = logging.getLogger(__name__)

# Keys whose values must never leave the process in a Sentry event. Matched
# case-insensitively against dict keys anywhere in the event tree. "refresh_token"
# is the important one: it lives in RQ job meta and is a long-lived Firebase
# credential the RQ integration would otherwise ship to Sentry on a job failure.
_SENSITIVE_KEYS = frozenset(
    {
        "refresh_token",
        "authorization",
        "x-firebase-appcheck",
        "id_token",
        "custom_token",
        "token",
        "password",
        "api_key",
        "firebase_api_key",
    }
)
_REDACTED = "[redacted]"


def _scrub(value):
    if isinstance(value, dict):
        return {
            k: (_REDACTED if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(v) for v in value)
    return value


def scrub_event(event, _hint=None):
    """Sentry `before_send` hook: redact sensitive values before events are sent.

    Walks the whole event tree and blanks any value under a sensitive key. Never
    raises — a throwing before_send would silently drop the event, so on any error
    we return the event unchanged rather than lose observability.
    """
    try:
        return _scrub(event)
    except Exception:  # pragma: no cover - defensive
        return event

# Max bytes for a single path component. Linux NAME_MAX is 255 bytes on the
# common filesystems; a longer name fails with ENAMETOOLONG.
MAX_PATH_COMPONENT_BYTES = 255


def sanitize_path_component(name):
    """Sanitize a user-supplied folder/filename into a single safe path component.

    Returns the cleaned name, or None if nothing usable remains. This is the
    Python twin of the umbrel (Rust) `sanitize_path_component` and must agree with
    it: keep names faithful — including '?' and non-ASCII, which are valid on our
    Linux/macOS volumes — strip only path separators and control characters so the
    result can't span directories, and reject empty / "." / ".." / over-NAME_MAX
    names. The caller's containment check (is_safe_path) is the traversal backstop.
    """
    if not name:
        return None
    cleaned = "".join(
        ch for ch in name
        if ch not in ("/", "\\") and unicodedata.category(ch) != "Cc"
    ).strip()
    if not cleaned or cleaned in (".", ".."):
        return None
    if len(cleaned.encode("utf-8")) > MAX_PATH_COMPONENT_BYTES:
        return None
    return cleaned


def remove_quietly(path):
    """Best-effort delete of a local artifact so failed work doesn't leak disk.

    A falsy path is a no-op. Missing files are fine (already cleaned/renamed);
    other OS errors (permissions, busy handles) are logged rather than raised so
    cleanup never masks the original outcome and disk leaks don't go unnoticed.
    """
    if not path:
        return
    try:
        os.remove(path)
        logger.debug(f"Removed file: {path}")
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"Failed to remove file {path}: {e}")
