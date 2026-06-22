"""Tests for the Python path-component sanitizer.

This is the twin of the umbrel (Rust) `sanitize_path_component` in
`titanic/src/upload.rs`; these cases intentionally mirror the Rust unit tests so
the two implementations can't silently drift.
"""

from fileutils import MAX_PATH_COMPONENT_BYTES, sanitize_path_component


def test_keeps_name_faithful_as_single_component():
    # The bug: "RV There Yet?" must not be rewritten to "RV There Yet".
    # Chars legal on our Linux/macOS volumes (including non-ASCII) are preserved;
    # only path separators and control chars are dropped so the result can't span
    # directories.
    assert sanitize_path_component("RV There Yet?") == "RV There Yet?"
    assert sanitize_path_component("進撃の巨人") == "進撃の巨人"
    assert sanitize_path_component("a/b\\c\0d") == "abcd"


def test_rejects_empty_and_traversal_names():
    # Containment contract: nothing that could become "current/parent dir" or
    # escape a single component may survive.
    for bad in ["", "   ", ".", "..", "/"]:
        assert sanitize_path_component(bad) is None, f"should reject {bad!r}"


def test_rejects_oversize_names_at_the_byte_boundary():
    # The limit is bytes, not characters: a 2-byte char counts as 2.
    max_name = "a" * MAX_PATH_COMPONENT_BYTES
    assert sanitize_path_component(max_name) == max_name
    assert sanitize_path_component("a" * (MAX_PATH_COMPONENT_BYTES + 1)) is None

    # 127 two-byte chars = 254 bytes (kept); 128 = 256 bytes (rejected).
    # U+00E9 (é) is a single code point that encodes to 2 UTF-8 bytes; use the
    # escape so the literal can't be stored decomposed (e + combining accent).
    two_byte = "é"
    assert len(two_byte.encode("utf-8")) == 2
    assert sanitize_path_component(two_byte * 127) == two_byte * 127
    assert sanitize_path_component(two_byte * 128) is None
