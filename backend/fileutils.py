import logging
import os
import unicodedata

logger = logging.getLogger(__name__)

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
