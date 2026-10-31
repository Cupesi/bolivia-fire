from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bolivia_fire.firms.quota import Quota


def test_sliding_window_backpressure(tmp_path: Path, synthetic_clock) -> None:
    quota = Quota(tmp_path, budget=5, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    quota.reserve(3)
    synthetic_clock.now += 100
    quota.reserve(2)
    quota.reserve(3)
    assert synthetic_clock.sleeps == [500]
    assert synthetic_clock.now == 1600


def test_two_coordinators_share_reservations(tmp_path: Path, synthetic_clock) -> None:
    first = Quota(tmp_path, budget=5, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    second = Quota(tmp_path, budget=5, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    first.reserve(5)
    second.reserve(1)
    assert synthetic_clock.sleeps == [600]


def test_separate_processes_preserve_all_reservations(tmp_path: Path) -> None:
    code = (
        "from pathlib import Path\n"
        "import sys\n"
        "from bolivia_fire.firms.quota import Quota\n"
        "quota = Quota(Path(sys.argv[1]))\n"
        "for index in range(25):\n"
        "    quota.reserve(1)\n"
    )
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode()
        assert stdout == b""
    events = json.loads((tmp_path / "firms_quota.json").read_text())["events"]
    assert len(events) == 50
    assert sum(event[1] for event in events) == 50


def test_reconcile_once_per_minute_and_external_usage(tmp_path: Path, synthetic_clock) -> None:
    calls = []

    def status():
        calls.append(synthetic_clock.time())
        return {
            "transaction_limit": 5000,
            "current_transactions": 3,
            "transaction_interval": "10 minutes",
        }

    first = Quota(tmp_path, budget=5, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    second = Quota(tmp_path, budget=5, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    assert first.reconcile(status)["current_transactions"] == 3
    synthetic_clock.now += 59
    second.reconcile(status)
    assert calls == [1000]
    synthetic_clock.now += 1
    second.reconcile(status)
    assert calls == [1000, 1060]
    first.reserve(2)
    assert synthetic_clock.now == 1660


def test_status_query_respects_full_budget(tmp_path: Path, synthetic_clock) -> None:
    calls = []

    def status():
        calls.append(synthetic_clock.time())
        return {
            "transaction_limit": 5000,
            "current_transactions": 5,
            "transaction_interval": "10 minutes",
        }

    quota = Quota(tmp_path, budget=5, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    quota.reconcile(status)
    synthetic_clock.now += 60
    quota.reconcile(status)
    assert calls == [1000, 1600]
    assert synthetic_clock.sleeps == [540]


def test_fresh_status_waits_for_minute_gate(tmp_path: Path, synthetic_clock) -> None:
    calls = []

    def status():
        calls.append(synthetic_clock.time())
        return {
            "transaction_limit": 5000,
            "current_transactions": 0,
            "transaction_interval": "10 minutes",
        }

    quota = Quota(tmp_path, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    quota.reconcile(status)
    quota.reconcile(status, fresh=True)
    assert calls == [1000, 1060]
    assert synthetic_clock.sleeps == [60]


def test_server_quota_exhaustion_sets_shared_cooldown(tmp_path: Path, synthetic_clock) -> None:
    quota = Quota(tmp_path, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    quota.exhausted(900)
    quota.reserve()
    assert synthetic_clock.sleeps == [900]


def test_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    quota = Quota(tmp_path)
    quota.path.write_text("broken")
    with pytest.raises(RuntimeError, match="corrupt"):
        quota.reserve()


@pytest.mark.parametrize("budget", [0, 5001])
def test_invalid_budget(tmp_path: Path, budget: int) -> None:
    with pytest.raises(ValueError, match="Budget"):
        Quota(tmp_path, budget=budget)
