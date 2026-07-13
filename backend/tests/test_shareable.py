"""Size-target budget math and compress_video artifact selection."""

import os

import pytest

import jobs.job as job
import jobs.shareable as shareable
from jobs.shareable import (
    audio_count_from_probe,
    duration_from_probe,
    fits_target,
    shareable_video_kbps,
    video_kbps_from_probe,
)


# ── budget math ──────────────────────────────────────────────────────


def test_budget_known_answer():
    # 10 MB over 60s: 10*8*1024/60 * 0.95 - 128 audio = 1169 kbps for video.
    assert shareable_video_kbps(10, 60) == 1169


def test_budget_capped_at_source_bitrate():
    assert shareable_video_kbps(10, 60, source_video_kbps=500) == 500
    # No cap when the source is richer than the budget.
    assert shareable_video_kbps(10, 60, source_video_kbps=99999) == 1169


def test_source_cap_never_lifts_the_floor():
    assert shareable_video_kbps(10, 60, source_video_kbps=30) == shareable.SHAREABLE_MIN_VIDEO_KBPS


def test_tiny_target_floors():
    assert shareable_video_kbps(0.1, 60) == shareable.SHAREABLE_MIN_VIDEO_KBPS


def test_budget_charges_each_preserved_audio_track():
    # 3 tracks: 1297 - 3*128 = 913 (vs 1169 single-track).
    assert shareable_video_kbps(10, 60, audio_streams=3) == 913


def test_fits_target_exact_boundary(tmp_path):
    f = tmp_path / "clip.mp4"
    one_kib_in_mb = 1 / 1024
    f.write_bytes(b"\0" * 1024)
    assert fits_target(str(f), one_kib_in_mb)
    f.write_bytes(b"\0" * 1025)
    assert not fits_target(str(f), one_kib_in_mb)


# ── probe extraction ─────────────────────────────────────────────────


def test_bitrate_from_video_stream():
    probe = {"streams": [{"codec_type": "video", "bit_rate": "5000000"}]}
    assert video_kbps_from_probe(probe) == 5000


def test_bitrate_falls_back_to_format():
    # MKV commonly omits per-stream bit_rate; overall bitrate is a valid upper bound.
    probe = {"streams": [{"codec_type": "video"}], "format": {"bit_rate": "3000000"}}
    assert video_kbps_from_probe(probe) == 3000


def test_bitrate_unknowable():
    assert video_kbps_from_probe({"streams": [], "format": {}}) is None


def test_duration_and_audio_count():
    probe = {
        "format": {"duration": "60.5"},
        "streams": [
            {"codec_type": "video"},
            {"codec_type": "audio"},
            {"codec_type": "audio"},
            {"codec_type": "subtitle"},
        ],
    }
    assert duration_from_probe(probe) == 60.5
    assert audio_count_from_probe(probe) == 2
    assert duration_from_probe({"format": {}}) is None


# ── compress_video artifact plan ─────────────────────────────────────


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
    monkeypatch.setattr(job, "is_h265_video", lambda f: False)
    monkeypatch.setattr(job, "_encode_full_quality", fake_full)
    monkeypatch.setattr(job, "build_shareable_copy", fake_shareable)
    return str(input_file), str(output_file), calls


def test_no_target_single_artifact(pipeline, monkeypatch):
    input_file, output_file, calls = pipeline
    assert job.compress_video(input_file) == [output_file]
    assert calls == {"full": 1, "shareable": []}
    assert not os.path.exists(input_file)


def test_target_skipped_when_output_fits(pipeline, monkeypatch):
    input_file, output_file, calls = pipeline
    monkeypatch.setattr(job, "fits_target", lambda p, t: True)
    assert job.compress_video(input_file, 10) == [output_file]
    assert calls["shareable"] == []


def test_target_builds_extra_copy_when_over(pipeline, monkeypatch):
    input_file, output_file, calls = pipeline
    monkeypatch.setattr(job, "fits_target", lambda p, t: False)
    result = job.compress_video(input_file, 10)
    assert result[0] == output_file
    assert result[1].endswith("clip_10MB.mp4")
    # Extra copy encodes from the pre-transcode source for best quality.
    assert calls["shareable"] == [{"source": input_file, "dest_file": None, "preserve": False}]


def test_shareable_only_skips_full_encode(pipeline, monkeypatch):
    input_file, output_file, calls = pipeline
    monkeypatch.setattr(job, "fits_target", lambda p, t: False)
    result = job.compress_video(input_file, 10, keep_full_quality=False)
    assert result == [output_file]
    assert calls["full"] == 0
    # Sole artifact lands under the original name, with all streams preserved.
    assert calls["shareable"] == [
        {"source": input_file, "dest_file": output_file, "preserve": True}
    ]
    assert not os.path.exists(input_file)


def test_shareable_only_with_fitting_source_uses_normal_pipeline(pipeline, monkeypatch):
    input_file, output_file, calls = pipeline
    monkeypatch.setattr(job, "fits_target", lambda p, t: True)
    result = job.compress_video(input_file, 10, keep_full_quality=False)
    assert result == [output_file]
    assert calls == {"full": 1, "shareable": []}


def test_shareable_only_when_only_reencode_overshoots(pipeline, monkeypatch):
    # Source fits the target but the CRF re-encode lands over it: the capped
    # copy must replace the full-quality file as the sole deliverable.
    input_file, output_file, calls = pipeline
    monkeypatch.setattr(job, "fits_target", lambda p, t: p == input_file)
    result = job.compress_video(input_file, 10, keep_full_quality=False)
    assert result == [output_file]
    assert calls["full"] == 1 and len(calls["shareable"]) == 1
    with open(output_file, "rb") as f:
        assert f.read() == b"s" * 16
    assert not os.path.exists(os.path.join(os.path.dirname(output_file), "clip_10MB.mp4"))
