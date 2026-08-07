"""Transcode metrics.

Tests breaking an upload, and publishing a wrong ratio.
"""

import os

import pytest

import jobs.job as job
import jobs.metrics as metrics


class _FakeCollection:
    """Captures writes instead of holding a Firestore client."""

    def __init__(self, writes):
        self.writes = writes

    def document(self, doc_id):
        return self

    def set(self, fields, merge=False, timeout=None):
        self.writes.append({"fields": fields, "merge": merge, "timeout": timeout})


@pytest.fixture
def writes(monkeypatch):
    captured = []
    monkeypatch.setattr(metrics, "_collection", lambda: _FakeCollection(captured))
    return captured


def document(writes):
    assert len(writes) == 1, f"expected one write per job, got {len(writes)}"
    return writes[0]["fields"]


# ── it must not break an upload ──────────────────────────────────────


def test_metrics_failure_does_not_fail_the_upload(pipeline, monkeypatch):
    # The one that actually costs a user something: a metrics table is not worth
    # dropping a video over. Covers every entry point, since they all write.
    class Exploding:
        def document(self, _id):
            raise RuntimeError("firestore is down")

    monkeypatch.setattr(metrics, "_collection", lambda: Exploding())
    input_file, output_file, _ = pipeline

    assert job.compress_video(input_file) == [output_file]
    assert os.path.exists(output_file)


def test_writes_cannot_hang_a_transcode(writes, tmp_path):
    # A try/except catches a Firestore error but not a stalled call, which would
    # add minutes to every job. The timeout is the only thing preventing that.
    metrics.JobRecord(str(tmp_path / "clip.mp4")).finish(metrics.STATUS_TRANSCODED)
    assert writes[0]["timeout"] == metrics.WRITE_TIMEOUT_SECONDS


# ── the ratio must not be quietly wrong ──────────────────────────────


def test_transcoded_records_both_sides(pipeline, writes):
    input_file, _, _ = pipeline
    # Distinct sizes, so a swapped source/output assignment fails here.
    with open(input_file, "wb") as f:
        f.write(b"i" * 100)

    job.compress_video(input_file)

    doc = document(writes)
    assert doc["status"] == metrics.STATUS_TRANSCODED
    # Read before the pipeline deleted the original — the reason the source side
    # is captured at construction rather than at finish.
    assert doc["source_bytes"] == 100
    assert doc["full_output_bytes"] == 64
    assert doc["encode_params"] == job._ENCODE_PARAMS


def test_already_hevc_is_not_counted_as_a_saving(pipeline, writes, monkeypatch):
    monkeypatch.setattr(job, "is_h265_video", lambda f: True)
    job.compress_video(pipeline[0])
    assert document(writes)["status"] == metrics.STATUS_SKIPPED_ALREADY_HEVC


def test_size_targeted_encode_is_not_counted_as_a_saving(pipeline, writes, monkeypatch):
    monkeypatch.setattr(job, "fits_target", lambda p, t: False)
    job.compress_video(pipeline[0], 10, keep_full_quality=False)
    assert document(writes)["status"] == metrics.STATUS_SHAREABLE_ONLY


def test_capped_copy_replacing_the_full_encode_is_not_counted_as_a_saving(
    pipeline, writes, monkeypatch
):
    # The branch that is easy to get wrong: the full encode runs, overshoots the
    # target, and the capped copy replaces it as the sole deliverable. What
    # shipped is size-targeted, so counting it as codec savings inflates the
    # ratio — and this path *looks* like a normal transcode from the outside.
    input_file, _, calls = pipeline
    monkeypatch.setattr(job, "fits_target", lambda p, t: p == input_file)

    job.compress_video(input_file, 10, keep_full_quality=False)

    assert calls["full"] == 1
    doc = document(writes)
    assert doc["status"] == metrics.STATUS_SHAREABLE_ONLY
    # Recorded before _deliver moved the copy onto output_file.
    assert doc["shareable_output_bytes"] == 16


def test_failed_encode_records_failure_and_still_raises(pipeline, writes, monkeypatch):
    def boom(source, output):
        raise RuntimeError("encode failed")

    monkeypatch.setattr(job, "_encode_full_quality", boom)
    with pytest.raises(RuntimeError):
        job.compress_video(pipeline[0])

    assert document(writes)["status"] == metrics.STATUS_FAILED


def test_reprocessing_updates_one_document(pipeline, writes):
    # RQ mints a fresh job id per enqueue, so the document id is derived from the
    # upload path instead. Without that a re-processed upload double-counts.
    input_file, _, _ = pipeline
    first = metrics.JobRecord(input_file).doc_id
    job.compress_video(input_file)

    with open(input_file, "wb") as f:  # the pipeline deleted the original
        f.write(b"i" * 64)
    job.compress_video(input_file)

    assert metrics.JobRecord(input_file).doc_id == first
    assert all(w["merge"] for w in writes)


# ── aggregation ──────────────────────────────────────────────────────


def _job(status=metrics.STATUS_TRANSCODED, source=1000, output=600, duration=60.0):
    return {
        "status": status,
        "source_bytes": source,
        "full_output_bytes": output,
        "duration_seconds": duration,
    }


def test_aggregate_known_answer():
    # 1000->600 and 2000->1000: saved 1400 of 3000 = 46.67%. Per-job ratios 0.6
    # and 0.5, median 0.55. 120s = 0.03h.
    assert metrics.aggregate([_job(), _job(source=2000, output=1000)]) == {
        "jobs_total": 2,
        "jobs_transcoded": 2,
        "source_bytes_total": 3000,
        "output_bytes_total": 1600,
        "bytes_saved": 1400,
        "savings_ratio": 0.4667,
        "median_transcode_ratio": 0.55,
        "hours_of_video": 0.03,
    }


def test_aggregate_excludes_everything_that_is_not_a_codec_saving():
    # The rule the whole feature rests on. A shareable copy compressed to a byte
    # budget would otherwise report a 99% saving; a job that died between accept
    # and finish has no output and would report 100%.
    result = metrics.aggregate(
        [
            _job(),
            _job(status=metrics.STATUS_SHAREABLE_ONLY, source=9000, output=100),
            _job(status=metrics.STATUS_SKIPPED_ALREADY_HEVC, source=9000, output=9000),
            _job(status=metrics.STATUS_FAILED, output=None),
            _job(output=None),
        ]
    )
    assert result["jobs_total"] == 5
    assert result["jobs_transcoded"] == 1
    assert result["savings_ratio"] == 0.4
    # Duration still counts for everything that went through the pipeline.
    assert result["hours_of_video"] == 0.08


def test_aggregate_of_nothing_is_zero_not_a_crash():
    # Day one, and any time the ratio is published before a transcode lands.
    result = metrics.aggregate([])
    assert result["savings_ratio"] == 0.0
    assert result["median_transcode_ratio"] == 0.0
