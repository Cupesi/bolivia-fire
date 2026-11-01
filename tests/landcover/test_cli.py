from __future__ import annotations

import argparse
import json
from pathlib import Path

from bolivia_fire.config import Paths
from bolivia_fire.landcover_cli import run


def test_synthetic_status_does_not_expose_ledger_project(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "usage.json"
    ledger.write_text(
        json.dumps(
            {
                "status": "paused",
                "submissions_authorized": False,
                "project": "synthetic-private-project",
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(operation="status", ledger=ledger)
    assert run(args, Paths(tmp_path)) == 0
    output = capsys.readouterr().out
    assert "synthetic-private-project" not in output
    assert json.loads(output)["submissions_authorized"] is False


def test_synthetic_paused_pilot_returns_before_credentials(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "usage.json"
    ledger.write_text(
        json.dumps({"status": "paused", "submissions_authorized": False}), encoding="utf-8"
    )
    args = argparse.Namespace(
        operation="pilot", ledger=ledger, verification_report=None, owner_attestation=None
    )
    assert run(args, Paths(tmp_path)) == 2
    assert "paused" in capsys.readouterr().err.lower()
