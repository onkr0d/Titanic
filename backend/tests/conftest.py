"""Shared fixtures.

The `pipeline` fixture is the house style for exercising `compress_video`: every
ffmpeg-heavy step is faked so the tests assert the *artifact plan* — which
encodes ran, from which source, what got delivered and cleaned up — without
invoking an encoder or needing media on disk.
"""

import os

import pytest

import jobs.job as job


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """Fake out ffmpeg-heavy steps; return (input_file, output_file, calls)."""
    uncompressed = tmp_path / "uncompressed"
    uncompressed.mkdir()
    (tmp_path / "compressed").mkdir()
    input_file = uncompressed / "clip.mp4"
    input_file.write_bytes(b"i" * 64)
    output_file = tmp_path / "compressed" / "clip.mp4"

    calls = {"full": 0, "shareable": []}

    def fake_full(source, output):
        calls["full"] += 1
        with open(output, "wb") as f:
            f.write(b"f" * 64)

    def fake_shareable(
        source, target, dest_dir, output_basename=None, *, dest_file=None, preserve_streams=False
    ):
        calls["shareable"].append(
            {"source": source, "dest_file": dest_file, "preserve": preserve_streams}
        )
        if dest_file is None:
            stem, ext = os.path.splitext(output_basename or os.path.basename(source))
            dest_file = os.path.join(dest_dir, f"{stem}_{int(target)}MB{ext}")
        with open(dest_file, "wb") as f:
            f.write(b"s" * 16)
        return dest_file

    monkeypatch.setattr(job, "process_audio_with_rnnoise", lambda i, o: None)
    monkeypatch.setattr(job, "_probe_quietly", lambda f: {})
    monkeypatch.setattr(job, "is_h265_video", lambda f: False)
    monkeypatch.setattr(job, "_encode_full_quality", fake_full)
    monkeypatch.setattr(job, "build_shareable_copy", fake_shareable)
    return str(input_file), str(output_file), calls
