from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from bolivia_fire.landcover.monitoring import UsageSnapshot, wait_for_settlement
from bolivia_fire.landcover.usage import UsagePaused
from tests.landcover.test_usage import synthetic_ledger


def test_synthetic_settlement_waits_for_latency_and_stability() -> None:
    elapsed = [0.0]
    ended = datetime(2026, 11, 1, 20, tzinfo=UTC)
    observed = []

    def read():
        observed.append(elapsed[0])
        total = 100 if elapsed[0] < 300 else 140
        return UsageSnapshot(total, ended, ended + timedelta(seconds=elapsed[0]))

    def sleep(seconds):
        elapsed[0] += seconds

    result = wait_for_settlement(
        read,
        baseline_seconds=100,
        completed_at=ended,
        latency_seconds=300,
        stable_seconds=120,
        timeout_seconds=600,
        poll_seconds=60,
        sleep=sleep,
        monotonic=lambda: elapsed[0],
        now=lambda: ended + timedelta(seconds=elapsed[0]),
    )
    assert result.total_seconds == 140
    assert observed[-1] == 420


@pytest.mark.parametrize("mode", ["unreadable", "unchanged", "unstable", "decreasing"])
def test_synthetic_unreliable_monitoring_refuses_settlement(mode) -> None:
    elapsed = [0.0]
    ended = datetime(2026, 11, 1, 20, tzinfo=UTC)

    def read():
        if mode == "unreadable":
            raise OSError("synthetic unavailable")
        total = {"unchanged": 100, "unstable": 101 + elapsed[0], "decreasing": 99}[mode]
        return UsageSnapshot(total, ended, ended + timedelta(seconds=elapsed[0]))

    def sleep(seconds):
        elapsed[0] += seconds

    with pytest.raises(UsagePaused):
        wait_for_settlement(
            read,
            baseline_seconds=100,
            completed_at=ended,
            latency_seconds=60,
            stable_seconds=120,
            timeout_seconds=180,
            poll_seconds=60,
            sleep=sleep,
            monotonic=lambda: elapsed[0],
            now=lambda: ended + timedelta(seconds=elapsed[0]),
        )


def test_synthetic_monitoring_replaces_total_without_double_counting_profiles(tmp_path) -> None:
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        workstream_safety_cap_eecu_hours=60,
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        monitoring_seed_per_tile_eecu_hours=0.01,
    )
    estimate = ledger.reserve_monitoring("synthetic-batch", 10, 100)
    assert estimate == pytest.approx(0.15)
    ledger.reconcile_monitoring("synthetic-batch", 3672, datetime.now(UTC).isoformat())
    state = ledger._load()
    assert state["workstream_eecu_hours"] == pytest.approx(1.02)
    assert state["project_month_eecu_hours"] == pytest.approx(1.02)
    assert state["batches"][0]["monitoring_delta_eecu_seconds"] == pytest.approx(72)
    assert state["batches"][0]["tile_count"] == 10
    assert state["batches"][0]["status"] == "monitoring_reconciled"
    assert ledger.reserve_monitoring("synthetic-next", 10, 90) == pytest.approx(0.03)


def test_synthetic_monitoring_cap_and_unknown_reservation_block_new_batch(tmp_path) -> None:
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        workstream_safety_cap_eecu_hours=60,
        workstream_eecu_hours=59.8,
        monitoring_seed_per_tile_eecu_hours=0.01,
    )
    with pytest.raises(UsagePaused, match="safety cap"):
        ledger.reserve_monitoring("synthetic-cap", 10, 100)
    assert not ledger._load()["batches"]
    state = ledger._load()
    state.update(status="active", submissions_authorized=True, workstream_eecu_hours=1)
    ledger.path.write_text(json.dumps(state), encoding="utf-8")
    ledger.reserve_monitoring("synthetic-first", 10, 100)
    with pytest.raises(UsagePaused, match="unreconciled"):
        ledger.reserve_monitoring("synthetic-second", 10, 90)


def test_synthetic_remaining_workstream_projection_blocks_a_small_batch(tmp_path) -> None:
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        workstream_safety_cap_eecu_hours=60,
        project_month_eecu_hours=45,
        workstream_eecu_hours=45,
        monitoring_seed_per_tile_eecu_hours=0.01,
        raster_pilot_authorized=False,
    )
    with pytest.raises(UsagePaused, match="plan"):
        ledger.reserve_monitoring("synthetic-remaining-over-cap", 10, 1000)
    assert ledger._load()["batches"] == []


def test_synthetic_authorized_pilot_can_measure_before_full_projection_gate(tmp_path) -> None:
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        workstream_safety_cap_eecu_hours=60,
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        monitoring_seed_per_tile_eecu_hours=0.02,
        raster_pilot_authorized=True,
    )
    assert ledger.reserve_monitoring("synthetic-pilot", 10, 2400) == pytest.approx(0.3)
    entry = ledger._load()["batches"][-1]
    assert entry["projected_remaining_eecu_hours_1_5x"] == pytest.approx(72)


@pytest.mark.parametrize("passed", [False, True])
def test_synthetic_twenty_tile_batches_require_both_pilot_gates(tmp_path, passed) -> None:
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        workstream_safety_cap_eecu_hours=60,
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        monitoring_seed_per_tile_eecu_hours=0.01,
        raster_pilot_authorized=False,
        monitoring_twenty_tile_batches_allowed=True,
        monitoring_expanded_batches_authorized=passed,
        raster_pilot_validation_passed=passed,
        raster_full_14_year_projection_eecu_hours_1_5x=10 if passed else None,
    )
    if passed:
        assert ledger.reserve_monitoring("synthetic-twenty", 20, 100) == pytest.approx(0.3)
    else:
        with pytest.raises(UsagePaused):
            ledger.reserve_monitoring("synthetic-premature-twenty", 20, 100)
        assert ledger._load()["batches"] == []


@pytest.mark.parametrize(("cap", "projection"), [(60, 10), (100, 90)])
def test_synthetic_year_batch_requires_pilot_proof_and_records_actual_submissions(
    tmp_path, cap, projection,
) -> None:
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        workstream_safety_cap_eecu_hours=cap,
        monitoring_seed_per_tile_eecu_hours=0.01,
        raster_pilot_authorized=False,
        monitoring_expanded_batches_authorized=True,
        raster_pilot_validation_passed=True,
        monitoring_year_sized_batches_allowed=True,
        raster_full_14_year_projection_eecu_hours_1_5x=projection,
    )
    assert ledger.monitoring_batch_limit() == 180
    assert ledger.reserve_monitoring("synthetic-year", 180, 180) == pytest.approx(2.7)
    ledger.reconcile_monitoring(
        "synthetic-year", 3672, datetime.now(UTC).isoformat(), submitted_tile_count=2
    )
    entry = ledger._load()["batches"][-1]
    assert entry["planned_tile_count"] == 180 and entry["tile_count"] == 2
    assert entry["monitoring_delta_eecu_seconds"] == pytest.approx(72)


@pytest.mark.parametrize(
    ("cap", "authorized", "validated", "projection", "expected"),
    [(100, True, True, 90, 360), (100, False, True, 90, 180),
     (60, True, True, 50, 180), (100, True, False, 90, 10),
     (100, True, True, 101, 10)],
)
def test_synthetic_two_year_batch_keeps_explicit_authorization_and_cost_gates(
    tmp_path, cap, authorized, validated, projection, expected,
) -> None:
    ledger = synthetic_ledger(
        tmp_path / "usage.json", workstream_safety_cap_eecu_hours=cap,
        monitoring_expanded_batches_authorized=True, raster_pilot_validation_passed=validated,
        raster_pilot_authorized=False, monitoring_year_sized_batches_allowed=True,
        monitoring_two_year_batches_allowed=authorized,
        raster_full_14_year_projection_eecu_hours_1_5x=projection,
    )
    assert ledger.monitoring_batch_limit() == expected


@pytest.mark.parametrize("authorization", ["missing", None])
def test_synthetic_missing_pilot_authorization_cannot_bypass_plan(tmp_path, authorization) -> None:
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        workstream_safety_cap_eecu_hours=60,
        monitoring_seed_per_tile_eecu_hours=0.02,
    )
    state = ledger._load()
    if authorization is None:
        state["raster_pilot_authorized"] = None
    else:
        state.pop("raster_pilot_authorized", None)
    ledger.path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(UsagePaused, match="plan"):
        ledger.reserve_monitoring("synthetic-missing-auth", 10, 2400)
    assert ledger._load()["batches"] == []


def test_synthetic_rate_and_reservation_share_one_locked_observation(tmp_path, monkeypatch) -> None:
    from bolivia_fire.landcover import usage

    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        workstream_safety_cap_eecu_hours=60,
        monitoring_seed_per_tile_eecu_hours=0.01,
        raster_pilot_authorized=False,
    )
    real_lock = usage.FileLock
    changed = [False]

    class ReconciledBeforeReservation:
        def __init__(self, *args, **kwargs):
            self.lock = real_lock(*args, **kwargs)

        def __enter__(self):
            self.lock.__enter__()
            if not changed[0]:
                state = ledger._load()
                state.update(project_month_eecu_hours=1.3, workstream_eecu_hours=1.3)
                state["batches"].append(
                    {
                        "batch": "synthetic-new-rate",
                        "status": "monitoring_reconciled",
                        "tile_count": 10,
                        "monitoring_delta_eecu_seconds": 1080,
                    }
                )
                usage.atomic_json(ledger.path, state)
                changed[0] = True
            return self

        def __exit__(self, *args):
            return self.lock.__exit__(*args)

    monkeypatch.setattr(usage, "FileLock", ReconciledBeforeReservation)
    assert ledger.reserve_monitoring("synthetic-atomic", 10, 100) == pytest.approx(0.45)
    entry = ledger._load()["batches"][-1]
    assert entry["monitoring_before_eecu_seconds"] == pytest.approx(4680)
    assert entry["projected_remaining_eecu_hours_1_5x"] == pytest.approx(4.5)
