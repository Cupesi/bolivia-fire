from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from shapely.geometry import box

from bolivia_fire.config import Paths
from bolivia_fire.landcover.monitoring import UsageSnapshot
from bolivia_fire.landcover.parallel import run_jobs
from bolivia_fire.landcover.raster import BANDS
from bolivia_fire.landcover.usage import UsagePaused
from tests.landcover.test_raster import synthetic_cells
from tests.landcover.test_usage import synthetic_ledger


def test_synthetic_parallel_jobs_are_bounded_and_consumed_once(tmp_path) -> None:
    lock = threading.Lock()
    active = [0]
    maximum = [0]
    consumed = []

    def compute(job):
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
        time.sleep(0.01)
        with lock:
            active[0] -= 1
        return {"error": None, "quota_error": False, "pixels": job["id"]}

    def consume(job, result):
        consumed.append(result["pixels"])
        return True

    report = run_jobs(
        [{"id": index} for index in range(12)],
        consume,
        root=tmp_path,
        maximum_workers=4,
        compute=compute,
        pool_factory=lambda count: ThreadPoolExecutor(max_workers=count),
    )
    assert sorted(consumed) == list(range(12))
    assert len(consumed) == len(set(consumed))
    assert maximum[0] == 4
    assert report["submitted"] == 12 and report["failed_error"] is None


def test_synthetic_quota_error_reduces_concurrency_and_retries_only_failed_job(tmp_path) -> None:
    attempts = {}
    consumed = []
    limits = []
    waits = []

    def compute(job):
        key = job["id"]
        attempts[key] = attempts.get(key, 0) + 1
        if key == 0 and attempts[key] == 1:
            return {"error": "synthetic HTTP 429 quota", "quota_error": True, "pixels": None}
        return {"error": None, "quota_error": False, "pixels": key}

    def consume(job, result):
        if result["error"]:
            return False
        consumed.append(result["pixels"])
        return True

    report = run_jobs(
        [{"id": index} for index in range(8)],
        consume,
        root=tmp_path,
        maximum_workers=4,
        compute=compute,
        pool_factory=lambda count: ThreadPoolExecutor(max_workers=count),
        on_rate_limit=lambda: limits.append(1),
        pause=waits.append,
    )
    assert sorted(consumed) == list(range(8))
    assert attempts == {0: 2, **{index: 1 for index in range(1, 8)}}
    assert limits == [1] and waits == [30]
    assert report["submitted"] == 9
    assert report["final_parallelism"] == 1 and report["failed_error"] is None


def test_synthetic_parallel_year_reuses_saved_tiles_and_primary_cost(tmp_path, monkeypatch) -> None:
    from bolivia_fire.landcover import parallel

    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        workstream_safety_cap_eecu_hours=60,
        monitoring_seed_per_tile_eecu_hours=0.01,
        raster_pilot_authorized=False,
        monitoring_expanded_batches_authorized=True,
        raster_pilot_validation_passed=True,
        monitoring_year_sized_batches_allowed=True,
        raster_full_14_year_projection_eecu_hours_1_5x=10,
    )
    observed = datetime.now(UTC)
    monkeypatch.setattr(
        parallel,
        "wait_for_settlement",
        lambda *args, **kwargs: UsageSnapshot(3602, observed, observed),
    )
    calls = []

    def synthetic_jobs(jobs, consume, **kwargs):
        calls.append(len(jobs))
        for job in jobs:
            pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
            pixels["forest"] = 1
            assert consume(
                {**job, "attempt": 1},
                {
                    "pixels": pixels,
                    "profile": "",
                    "error": None,
                    "profile_error": "Profile not found",
                    "quota_error": False,
                    "runtime_seconds": 1,
                },
            )
        return {
            "submitted": len(jobs),
            "quota_errors": 0,
            "final_parallelism": 4,
            "failed_error": None,
        }

    monkeypatch.setattr(parallel, "run_jobs", synthetic_jobs)
    session = SimpleNamespace(
        ledger=ledger,
        paths=paths,
        project="synthetic-project",
        transform=[0.000269, 0, 0, 0, -0.000269, 0],
        monitoring=SimpleNamespace(read=lambda: None),
        resources=SimpleNamespace(check=lambda: None, peak_rss_bytes=1, minimum_free_bytes=10**12),
    )
    for expected_reused in (0, 1):
        frame, _, report = parallel.parallel_raster_year(
            session,
            synthetic_cells(),
            box(0, 0, 0.1, 0.05),
            2013,
        )
        assert frame.mapbiomas_forest_fraction.tolist() == [1, 1]
        assert report["reused_tiles"] == expected_reused
        assert report["reported_eecu_seconds"] == 2
    assert calls == [1]


def test_synthetic_profile_failure_does_not_hide_compute_quota_error(monkeypatch) -> None:
    from bolivia_fire.landcover import parallel

    ee = MagicMock()
    ee.data.computePixels.side_effect = RuntimeError("synthetic HTTP 429 quota")

    @contextmanager
    def profile(*, destination):
        try:
            yield
        finally:
            raise RuntimeError("Profile not found")

    ee.profilePrinting.side_effect = profile
    monkeypatch.setattr(parallel, "_worker_ee", ee)
    monkeypatch.setattr(parallel, "_worker_error", None)
    monkeypatch.setattr(parallel, "_worker_project", "synthetic-project")
    result = parallel.compute_tile(
        {
            "year": 2013,
            "geometry": {},
            "grid": {"dimensions": {"height": 1, "width": 1}},
            "transform": [0.05, 0, 0, 0, -0.05, 0],
        }
    )
    assert result["quota_error"] is True
    assert "429" in result["error"]
    assert result["profile_error"] == "Profile not found"


def test_synthetic_six_request_backoff_steps_to_four_then_two(tmp_path) -> None:
    attempts = {}
    limits = []

    def compute(job):
        index = job["id"]
        attempts[index] = attempts.get(index, 0) + 1
        failed = index in (0, 1) and attempts[index] == 1
        return {
            "pixels": None if failed else index,
            "quota_error": failed,
            "error": "synthetic HTTP 429" if failed else None,
        }

    report = run_jobs(
        [{"id": index} for index in range(12)],
        lambda job, result: result["pixels"] is not None,
        root=tmp_path,
        maximum_workers=6,
        backoff_steps=(4, 2),
        compute=compute,
        pool_factory=lambda count: ThreadPoolExecutor(max_workers=count),
        on_rate_limit=lambda: limits.append(4 if not limits else 2),
        pause=lambda seconds: None,
    )
    assert limits == [4, 2] and report["final_parallelism"] == 2
    assert report["submitted"] == 14 and report["failed_error"] is None
    assert attempts == {0: 2, 1: 2, **{index: 1 for index in range(2, 12)}}


def test_synthetic_two_year_group_reserves_and_settles_once_and_resumes(
    tmp_path, monkeypatch
) -> None:
    from bolivia_fire.landcover import parallel

    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        workstream_safety_cap_eecu_hours=100,
        monitoring_seed_per_tile_eecu_hours=0.01,
        raster_pilot_authorized=False,
        monitoring_expanded_batches_authorized=True,
        raster_pilot_validation_passed=True,
        monitoring_year_sized_batches_allowed=True,
        monitoring_two_year_batches_allowed=True,
        monitoring_max_parallel_requests=6,
        monitoring_quota_backoff_steps=[4, 2],
        raster_full_14_year_projection_eecu_hours_1_5x=90,
    )
    assert ledger.monitoring_batch_limit() == 360
    observed = datetime.now(UTC)
    settled = MagicMock(return_value=UsageSnapshot(3672, observed, observed))
    monkeypatch.setattr(parallel, "wait_for_settlement", settled)
    submitted = []

    def synthetic_jobs(jobs, consume, **kwargs):
        assert kwargs["maximum_workers"] == 6
        for job in jobs:
            submitted.append(job["year"])
            pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
            pixels["forest"] = 1
            assert consume(
                {**job, "attempt": 1},
                {
                    "pixels": pixels,
                    "profile": "",
                    "error": None,
                    "quota_error": False,
                    "profile_error": "Profile not found",
                    "runtime_seconds": 1,
                },
            )
        return {
            "submitted": len(jobs),
            "quota_errors": 0,
            "final_parallelism": 6,
            "failed_error": None,
        }

    monkeypatch.setattr(parallel, "run_jobs", synthetic_jobs)
    session = SimpleNamespace(
        ledger=ledger,
        paths=paths,
        project="synthetic-project",
        transform=[0.000269, 0, 0, 0, -0.000269, 0],
        monitoring=SimpleNamespace(read=lambda: None),
        resources=SimpleNamespace(check=lambda: None, peak_rss_bytes=1, minimum_free_bytes=10**12),
    )
    for year in (2024, 2025):
        frame, _, report = parallel.parallel_raster_year(
            session, synthetic_cells(), box(0, 0, 0.1, 0.05), year
        )
        assert frame.year.eq(year).all()
        assert report["reported_eecu_seconds"] == 36
        assert "allocation" in report["year_cost_scope"]
    assert submitted == [2024, 2025] and settled.call_count == 1
    entry = ledger._load()["batches"][-1]
    assert entry["years"] == [2024, 2025]
    assert entry["tile_count"] == entry["completed_tiles"] == 2
    assert entry["monitoring_delta_eecu_seconds"] == 72
    del session._raster_year_cache
    _, _, restored = parallel.parallel_raster_year(
        session, synthetic_cells(), box(0, 0, 0.1, 0.05), 2025
    )
    assert restored["compute_pixels_requests"] == 0 and restored["reused_tiles"] == 1
    assert restored["reported_eecu_seconds"] == 36
    assert submitted == [2024, 2025] and settled.call_count == 1


def test_synthetic_partial_pair_accounts_overhead_and_retries_only_unsaved_tiles(
    tmp_path,
    monkeypatch,
) -> None:
    from bolivia_fire.landcover import parallel

    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        workstream_safety_cap_eecu_hours=100,
        monitoring_seed_per_tile_eecu_hours=0.01,
        raster_pilot_authorized=False,
        monitoring_expanded_batches_authorized=True,
        raster_pilot_validation_passed=True,
        monitoring_two_year_batches_allowed=True,
        monitoring_max_parallel_requests=6,
        raster_full_14_year_projection_eecu_hours_1_5x=90,
    )
    observed = datetime.now(UTC)
    settled = MagicMock(
        side_effect=[
            UsageSnapshot(3672, observed, observed),
            UsageSnapshot(3674, observed, observed),
        ]
    )
    monkeypatch.setattr(parallel, "wait_for_settlement", settled)
    submitted = []
    fail_once = [True]

    def synthetic_jobs(jobs, consume, **kwargs):
        failure = None
        for job in jobs:
            submitted.append(job["year"])
            failed = job["year"] == 2025 and fail_once[0]
            if failed:
                fail_once[0] = False
                failure = "synthetic failed tile"
            pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
            pixels["forest"] = 1
            consume(
                {**job, "attempt": 1},
                {
                    "pixels": None if failed else pixels,
                    "profile": "",
                    "error": failure,
                    "quota_error": False,
                },
            )
        return {
            "submitted": len(jobs),
            "quota_errors": 0,
            "final_parallelism": 6,
            "failed_error": failure,
        }

    monkeypatch.setattr(parallel, "run_jobs", synthetic_jobs)
    session = SimpleNamespace(
        ledger=ledger,
        paths=paths,
        project="synthetic-project",
        transform=[0.000269, 0, 0, 0, -0.000269, 0],
        monitoring=SimpleNamespace(read=lambda: None),
        resources=SimpleNamespace(check=lambda: None, peak_rss_bytes=1, minimum_free_bytes=10**12),
    )
    with pytest.raises(UsagePaused, match="synthetic failed tile"):
        parallel.parallel_raster_year(session, synthetic_cells(), box(0, 0, 0.1, 0.05), 2024)
    first = ledger._load()["batches"][0]
    assert first["attempted_tiles"] == 2 and first["completed_tiles"] == 1
    assert first["rate_sample_complete"] is False
    assert first["partial_batch_overhead_eecu_seconds"] == 72
    assert session._raster_year_cache == {}
    assert not hasattr(session, "_active_monitoring_group")
    _, _, restored = parallel.parallel_raster_year(
        session, synthetic_cells(), box(0, 0, 0.1, 0.05), 2024
    )
    assert submitted == [2024, 2025, 2025]
    assert settled.call_count == 2
    assert restored["reused_tiles"] == 1 and restored["reported_eecu_seconds"] == 37
    entry = ledger._load()["batches"][-1]
    assert entry["years"] == [2024, 2025]
    assert entry["tile_count"] == entry["completed_tiles"] == 1
    assert entry["monitoring_delta_eecu_seconds"] == 2
