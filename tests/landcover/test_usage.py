from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bolivia_fire.landcover.usage import UsageLedger, UsagePaused, parse_profile, redact_project


def synthetic_ledger(path: Path, **overrides: object) -> UsageLedger:
    record = {
        "status": "active",
        "submissions_authorized": True,
        "project_monthly_budget_eecu_hours": 150,
        "workstream_safety_cap_eecu_hours": 30,
        "project_month_eecu_hours": 10.0,
        "workstream_eecu_hours": 1.0,
        "usage_observed_at_utc": datetime.now(UTC).isoformat(),
        "project_month_start_utc": "2026-11-01T07:00:00Z",
        "unreconciled_overhead_reserve_eecu_hours": 0.1,
        "batches": [],
        **overrides,
    }
    path.write_text(json.dumps(record), encoding="utf-8")
    return UsageLedger(path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_month_eecu_hours": None},
        {"workstream_eecu_hours": None},
        {"workstream_eecu_hours": 29.9},
        {"project_month_eecu_hours": 149.9},
        {"submissions_authorized": False},
        {"status": "paused"},
        {"usage_observed_at_utc": "2026-01-01T00:00:00Z"},
    ],
)
def test_synthetic_unknown_exhausted_or_paused_usage_refuses_submission(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    ledger = synthetic_ledger(tmp_path / "usage.json", **overrides)
    with pytest.raises(UsagePaused):
        ledger.reserve("synthetic-batch", 0.2)
    saved = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert saved["submissions_authorized"] is False
    assert saved["status"] == "paused"


def test_synthetic_pending_request_blocks_duplicate_then_records_measured_cost(
    tmp_path: Path,
) -> None:
    ledger = synthetic_ledger(tmp_path / "usage.json")
    ledger.reserve("synthetic-batch", 0.2)
    with pytest.raises(UsagePaused):
        ledger.reserve("synthetic-duplicate", 0.2)
    ledger.reconcile("synthetic-batch", reported_eecu_seconds=3.0, upper_eecu_seconds=3.01)
    saved = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert saved["workstream_eecu_hours"] == pytest.approx(1 + 3.01 / 3600)
    assert saved["project_month_eecu_hours"] == pytest.approx(10 + 3.01 / 3600)
    assert saved["batches"][0]["reported_eecu_seconds"] == 3
    assert saved["batches"][0]["status"] == "measured"


def test_synthetic_missing_profile_pauses_and_retains_reservation(tmp_path: Path) -> None:
    ledger = synthetic_ledger(tmp_path / "usage.json")
    ledger.reserve("synthetic-batch", 0.2)
    with pytest.raises(UsagePaused):
        ledger.reconcile("synthetic-batch", reported_eecu_seconds=None, upper_eecu_seconds=None)
    saved = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert saved["workstream_eecu_hours"] is None
    assert saved["last_known_workstream_eecu_hours"] == 1
    assert saved["usage_unknown_batch"] == "synthetic-batch"
    assert saved["status"] == "paused"
    assert saved["batches"][0]["reserved_eecu_hours"] == 0.2


def test_synthetic_profile_rounding_is_conservative() -> None:
    profile = (
        " EECU\u00b7s PeakMem Count Description\n 0.023 57k 54 (plumbing)\n"
        " 0.001 64k 3 Loading\n 0.000 3k 2 Reduce\n - 12k 1 Asset\n"
    )
    result = parse_profile(profile)
    assert result.reported_eecu_seconds == pytest.approx(0.024)
    assert result.upper_eecu_seconds == pytest.approx(0.027)


@pytest.mark.parametrize("profile", ["", "unavailable", "EECU seconds: unknown"])
def test_synthetic_unmeasured_profile_is_rejected(profile: str) -> None:
    with pytest.raises(UsagePaused):
        parse_profile(profile)


def test_synthetic_redaction_preserves_measured_decimal_costs() -> None:
    source = "projects/synthetic-project: 0.010478857280721347; ?MAP_KEY=synthetic-secret"
    result = redact_project(source, "synthetic-project")
    assert "synthetic-project" not in result
    assert "synthetic-secret" not in result
    assert "0.010478857280721347" in result


def test_synthetic_expired_observation_does_not_allow_new_work(tmp_path: Path) -> None:
    timestamp = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    ledger = synthetic_ledger(tmp_path / "usage.json", usage_observed_at_utc=timestamp)
    with pytest.raises(UsagePaused):
        ledger.reserve("synthetic-batch", 0.1)


def test_synthetic_plan_over_cap_refuses_small_first_batch(tmp_path: Path) -> None:
    ledger = synthetic_ledger(tmp_path / "usage.json", full_aggregation_estimate_eecu_hours=40.1)
    with pytest.raises(UsagePaused, match="plan"):
        ledger.reserve("synthetic-batch", 0.1)


def test_synthetic_complete_profiles_advance_continuous_accounting(tmp_path: Path) -> None:
    ledger = synthetic_ledger(tmp_path / "usage.json")
    ledger.reserve("synthetic-first", 0.1)
    ledger.reconcile("synthetic-first", reported_eecu_seconds=1, upper_eecu_seconds=1.001)
    state = json.loads(ledger.path.read_text("utf-8"))
    assert "profile_usage_observed_at_utc" in state
    original = state["usage_observed_at_utc"]
    state["usage_observed_at_utc"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    ledger.path.write_text(json.dumps(state), encoding="utf-8")
    ledger.reserve("synthetic-second", 0.1)
    saved = json.loads(ledger.path.read_text("utf-8"))
    assert saved["usage_observed_at_utc"] != original
    assert saved["usage_observed_at_utc"] == state["usage_observed_at_utc"]
    assert saved["batches"][-1]["batch"] == "synthetic-second"
