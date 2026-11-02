from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from bolivia_fire.landcover.earthengine import ResourceMonitor, normalize_features
from bolivia_fire.landcover.usage import UsagePaused


def test_synthetic_missing_feature_is_retained_as_missing() -> None:
    features = [
        {
            "type": "Feature",
            "geometry": None,
            "properties": {
                "cell_id": "synthetic-cell-a",
                "groups": [{"class_code": 3, "sum": 60}, {"class_code": 15, "sum": 40}],
            },
        }
    ]
    frame = normalize_features(features, ["synthetic-cell-a", "synthetic-cell-b"], 2012)
    assert frame.cell_id.tolist() == ["synthetic-cell-a", "synthetic-cell-b"]
    assert frame.year.tolist() == [2012, 2012]
    assert frame.iloc[0].mapbiomas_forest_fraction == pytest.approx(0.6)
    assert bool(frame.iloc[0].response_feature_present) is True
    assert bool(frame.iloc[1].response_feature_present) is False
    assert bool(frame.iloc[1].available) is False
    assert frame.iloc[1].isna()["mapbiomas_forest_fraction"]


def test_synthetic_unrequested_and_duplicate_features_are_rejected() -> None:
    feature = {
        "type": "Feature",
        "geometry": None,
        "properties": {"cell_id": "synthetic-unrequested", "groups": []},
    }
    with pytest.raises(ValueError, match="Unrequested"):
        normalize_features([feature], ["synthetic-requested"], 2012)
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_features([feature, feature], ["synthetic-unrequested"], 2012)


def test_synthetic_duplicate_class_groups_are_added() -> None:
    feature = {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "cell_id": "synthetic-cell",
            "groups": [
                {"class_code": 3, "sum": 20},
                {"class_code": 3, "sum": 30},
                {"class_code": 15, "sum": 50},
            ],
        },
    }
    frame = normalize_features([feature], ["synthetic-cell"], 2012)
    assert frame.iloc[0].mapbiomas_forest_fraction == 0.5


def test_synthetic_exited_child_does_not_invalidate_resource_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exited_child_memory() -> None:
        raise psutil.NoSuchProcess(991)

    parent = SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=128),
        children=lambda **kwargs: [
            SimpleNamespace(memory_info=exited_child_memory),
            SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=256)),
        ],
    )
    monkeypatch.setattr(psutil, "Process", lambda: parent)
    monitor = ResourceMonitor(tmp_path)
    monitor.check()
    assert monitor.error is None
    assert monitor.peak_rss_bytes == 384


@pytest.mark.parametrize("failure", [psutil.AccessDenied(992), psutil.NoSuchProcess(993)])
def test_synthetic_parent_measurement_failure_still_pauses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    def unreadable_parent_memory() -> None:
        raise failure

    parent = SimpleNamespace(memory_info=unreadable_parent_memory, children=lambda **kwargs: [])
    monkeypatch.setattr(psutil, "Process", lambda: parent)
    with pytest.raises(UsagePaused, match="resource monitoring failed"):
        ResourceMonitor(tmp_path).check()


def test_synthetic_unreadable_live_child_still_pauses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable_child_memory() -> None:
        raise psutil.AccessDenied(994)

    parent = SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=128),
        children=lambda **kwargs: [SimpleNamespace(memory_info=unreadable_child_memory)],
    )
    monkeypatch.setattr(psutil, "Process", lambda: parent)
    with pytest.raises(UsagePaused, match="resource monitoring failed"):
        ResourceMonitor(tmp_path).check()


def test_synthetic_memory_limit_still_pauses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=12 * 1024**3), children=lambda **kwargs: [],
    )
    monkeypatch.setattr(psutil, "Process", lambda: parent)
    with pytest.raises(UsagePaused, match="12-GiB"):
        ResourceMonitor(tmp_path).check()
