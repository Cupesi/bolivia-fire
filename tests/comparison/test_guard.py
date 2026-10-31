from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from bolivia_fire.config import REPO_ROOT, Paths


@pytest.fixture
def synthetic_comparison(tmp_path: Path) -> Paths:
    configs = tmp_path / "configs"
    configs.mkdir()
    for name in ("protocol.yaml", "experiments.yaml", "comparison.yaml"):
        shutil.copyfile(REPO_ROOT / "configs" / name, configs / name)
    model = tmp_path / "artifacts/models/comparison/synthetic.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"synthetic fitted model")
    panel = tmp_path / "datasets/processed/panel/year=2023/part.parquet"
    panel.parent.mkdir(parents=True)
    panel.write_bytes(b"synthetic panel snapshot")
    return Paths(tmp_path)


def synthetic_freeze(paths: Paths) -> dict:
    from bolivia_fire.comparison.guard import freeze_selection

    name = "artifacts/models/comparison/synthetic.bin"
    return freeze_selection(
        paths,
        {
            "artifact_hashes": {name: hashlib.sha256((paths.root / name).read_bytes()).hexdigest()},
            "input_hashes": {},
            "decision": "Synthetic validation-only decision",
        },
    )


@pytest.mark.parametrize(("flag", "reason"), [(False, "exploratory"), (True, None), (True, " ")])
def test_synthetic_guard_refuses_missing_flag_or_reason(
    synthetic_comparison: Paths,
    flag: bool,
    reason: str | None,
) -> None:
    from bolivia_fire.comparison.guard import open_test

    paths = synthetic_comparison
    synthetic_freeze(paths)
    with pytest.raises(ValueError, match="post-hoc-test|reason"):
        open_test(paths, post_hoc_test=flag, reason=reason)
    assert not (paths.root / "artifacts/evaluation/final_test_log.json").exists()


def test_synthetic_guard_requires_selection_before_test(synthetic_comparison: Paths) -> None:
    from bolivia_fire.comparison.guard import open_test

    with pytest.raises(ValueError, match="selection"):
        open_test(synthetic_comparison, post_hoc_test=True, reason="exploratory")


def test_synthetic_single_opening_preserves_primary_entries(synthetic_comparison: Paths) -> None:
    from bolivia_fire.comparison.guard import open_test

    paths = synthetic_comparison
    synthetic_freeze(paths)
    log = paths.root / "artifacts/evaluation/final_test_log.json"
    primary = {"type": "primary", "synthetic": True}
    log.write_text(json.dumps([primary]), encoding="utf-8")
    entry = open_test(paths, post_hoc_test=True, reason="exploratory")
    entries = json.loads(log.read_text(encoding="utf-8"))
    assert entries == [primary, entry]
    assert entry["type"] == "post_hoc_comparison"
    assert entry["reason"] == "exploratory"
    assert len(entry["comparison_selection_sha256"]) == 64
    assert len(entry["panel_snapshot_sha256"]) == 64
    assert set(entry["config_sha256"]) == {"protocol", "experiments", "comparison"}
    assert "artifacts/models/comparison/synthetic.bin" in entry["model_hashes"]
    with pytest.raises(ValueError, match="reopen-reason"):
        open_test(paths, post_hoc_test=True, reason="exploratory")
    with pytest.raises(ValueError, match="reopen-reason"):
        open_test(paths, post_hoc_test=True, reason="exploratory", reopen_reason=" ")
    reopened = open_test(paths, post_hoc_test=True, reason="exploratory", reopen_reason="audit")
    assert reopened["reopening"] is True
    assert reopened["reopen_reason"] == "audit"
    assert len(json.loads(log.read_text(encoding="utf-8"))) == 3


@pytest.mark.parametrize("changed", ["selection", "model", "config"])
def test_synthetic_guard_refuses_changed_frozen_inputs(
    synthetic_comparison: Paths,
    changed: str,
) -> None:
    from bolivia_fire.comparison.guard import open_test

    paths = synthetic_comparison
    synthetic_freeze(paths)
    name = {
        "selection": "artifacts/evaluation/comparison_selection.json",
        "model": "artifacts/models/comparison/synthetic.bin",
        "config": "configs/comparison.yaml",
    }[changed]
    with (paths.root / name).open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="changed|hash"):
        open_test(paths, post_hoc_test=True, reason="exploratory")
    assert not (paths.root / "artifacts/evaluation/final_test_log.json").exists()


def test_synthetic_selection_cannot_be_overwritten(synthetic_comparison: Paths) -> None:
    synthetic_freeze(synthetic_comparison)
    with pytest.raises(ValueError, match="frozen|exists"):
        synthetic_freeze(synthetic_comparison)
