import logging
import os

logger = logging.getLogger(__name__)


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
