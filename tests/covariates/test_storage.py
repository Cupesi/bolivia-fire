from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from bolivia_fire.covariates.chirps import extract_year
from bolivia_fire.covariates.storage import Budget, atomic_json, read_json


def test_synthetic_atomic_restart_and_manifest(tmp_path):
    calls = []

    def synthetic_reader(day, budget):
        calls.append(day)
        return np.ones((2, 3), dtype=np.float32), {
            "url": "https://synthetic.invalid/rain.cog", "http_size": 1000,
            "transform": [0.05, 0, -70, 0, -0.05, -9], "nodata_count": 0,
        }

    first = extract_year(2024, tmp_path, reader=synthetic_reader, end=date(2024, 1, 2))
    assert first["days"] == 2
    assert len(calls) == 2
    second = extract_year(2024, tmp_path, reader=synthetic_reader, end=date(2024, 1, 2))
    assert second["days"] == 2
    assert len(calls) == 2
    records = (tmp_path / "chirps_manifest.jsonl").read_text().splitlines()
    assert len(records) == 2
    assert '"window_sha256"' in records[0]
    assert not list(tmp_path.rglob("*.part"))


def test_synthetic_failure_does_not_promote_year(tmp_path):
    calls = 0

    def synthetic_failure(day, budget):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic failure")
        return np.ones((2, 3), dtype=np.float32), {"url": "synthetic", "http_size": 1000}

    with pytest.raises(RuntimeError, match="synthetic failure"):
        extract_year(2024, tmp_path, reader=synthetic_failure, end=date(2024, 1, 2))
    assert not (tmp_path / "chirps_daily" / "year=2024" / "complete.json").exists()


def test_synthetic_completed_interval_can_extend_without_refetch(tmp_path):
    calls = []

    def synthetic_reader(day, budget):
        calls.append(day)
        return np.ones((2, 3), dtype=np.float32), {"url": "synthetic", "http_size": 1000}

    extract_year(2024, tmp_path, reader=synthetic_reader, end=date(2024, 1, 1))
    result = extract_year(2024, tmp_path, reader=synthetic_reader, end=date(2024, 1, 2))
    assert result["days"] == 2
    assert calls == [date(2024, 1, 1), date(2024, 1, 2)]


def test_synthetic_budget_survives_restart_and_atomic_metadata(tmp_path):
    budget = Budget(tmp_path, transfer_limit=100, storage_limit=100)
    budget.consume(60)
    with pytest.raises(RuntimeError, match="transfer budget"):
        budget.consume(41)
    assert Budget(tmp_path, transfer_limit=100).transferred == 60
    atomic_json(tmp_path / "synthetic.json", {"days": 2})
    assert read_json(tmp_path / "synthetic.json") == {"days": 2}


def test_synthetic_free_space_floor(tmp_path, monkeypatch):
    monkeypatch.setattr("bolivia_fire.covariates.storage.shutil.disk_usage",
                        lambda path: SimpleNamespace(free=54_999_999_999))
    with pytest.raises(RuntimeError, match="free space"):
        Budget(tmp_path).check_storage()


def test_synthetic_cooldown_backoff_persistence_and_circuit_breaker(tmp_path, monkeypatch):
    monkeypatch.setattr("bolivia_fire.covariates.storage.time.time", lambda: 1000)
    budget = Budget(tmp_path)
    first = budget.record_throttle(jitter=False)
    assert first["cooldown_until"] == 1600
    assert Budget(tmp_path).cooldown_remaining == 600
    assert budget.record_throttle(jitter=False)["cooldown_until"] == 2200
    for _ in range(3):
        budget.record_throttle(jitter=False)
    with pytest.raises(RuntimeError, match="circuit breaker"):
        budget.record_throttle(jitter=False)
    budget.record_success()
    assert budget.cooldown_remaining == 0
    assert budget.record_throttle(retry_after="120", jitter=False)["cooldown_until"] == 1120
