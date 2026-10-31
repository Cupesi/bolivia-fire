from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pipeline_fixtures import pin_synthetic_outages, publish_synthetic_bundle
from shapely.geometry import box

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.detections import _canonical_partitions
from bolivia_fire.features import DailyHistory, TrainingClimatology, week_index
from bolivia_fire.grid import coordinate_indices, holdout_blocks
from bolivia_fire.labels import labels_for_issue


def test_synthetic_outage_union_threshold_and_midnight() -> None:
    from bolivia_fire.outages import observation_days, parse_outages

    html = """<table><tr><th>Year-Day</th></tr>
    <tr><td>2024-001</td><td>January 1, 2024</td><td>01:00:00</td>
    <td>05:00:00</td><td>synthetic overlap</td></tr>
    <tr><td>2024-001</td><td>January 1, 2024</td><td>03:00:00</td>
    <td>07:00:00</td><td>synthetic overlap</td></tr>
    <tr><td>2024-002 - 2024-003</td><td>January 2, 2024 - January 3, 2024</td>
    <td>23:00:00</td><td>00:00:00</td><td>synthetic midnight</td></tr></table>"""
    outages = parse_outages(html)
    days = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=3), "acquired": True})
    result = observation_days(days, outages)
    assert len(outages) == 3
    assert str(outages.end_utc.iloc[-1]) == "2024-01-03 00:00:00+00:00"
    assert result.outage_hours.tolist() == [6, 1, 0]
    assert result.observed.tolist() == [False, True, True]


def test_synthetic_acquired_outage_day_nulls_label() -> None:
    detections = pd.DataFrame(
        {
            "cell_id": pd.Series(dtype="string"),
            "acq_datetime": pd.Series(dtype="datetime64[ns, UTC]"),
            "in_bolivia": pd.Series(dtype=bool),
            "type": pd.Series(dtype=int),
            "confidence": pd.Series(dtype="string"),
        }
    )
    days = pd.DataFrame(
        {
            "date": pd.date_range("2022-08-01", periods=7),
            "acquired": True,
            "observed": False,
            "bbox_detection_count": 0,
        }
    )
    result = labels_for_issue(pd.DataFrame({"cell_id": ["a"]}), detections, days, "2022-08-01")
    assert result.y_primary.isna().all()
    assert not result.target_complete.any()


@pytest.mark.parametrize("identity", [{}, {"bbox": [-64, -18, -63, -17]}])
def test_synthetic_non_national_identity_is_rejected(identity: dict) -> None:
    record = {
        "product": "VIIRS_SNPP_SP",
        "start": "2024-09-01",
        "day_range": 5,
        "outcome": "ok_empty",
        "path": "synthetic.csv",
        "sha256": "synthetic",
        "rows": 0,
        **identity,
    }
    chosen, rejected = _canonical_partitions([record], load_protocol())
    assert chosen == []
    assert rejected[0]["reason"] == "request_identity_mismatch"


@pytest.mark.parametrize(("day", "expected"), [("2018-12-24", 51), ("2018-12-31", 51)])
def test_synthetic_calendar_uses_one_week_index(day: str, expected: int) -> None:
    issue = pd.Timestamp(day)
    assert week_index(issue) == expected
    cells = pd.DataFrame({"cell_id": ["a"], "row": [1], "col": [1]})
    detections = pd.DataFrame(
        {
            "cell_id": pd.Series(dtype=str),
            "acq_datetime": pd.Series(dtype="datetime64[ns, UTC]"),
            "in_bolivia": pd.Series(dtype=bool),
            "type": pd.Series(dtype=int),
            "confidence": pd.Series(dtype=str),
            "frp": pd.Series(dtype=float),
        }
    )
    days = pd.DataFrame(
        {
            "date": pd.date_range(issue - pd.Timedelta(days=365), issue),
            "acquired": True,
            "observed": True,
        }
    )
    days.loc[days.index[-2], "observed"] = False
    result = DailyHistory(cells, detections, days, days.date.min(), issue).features(issue)
    assert result.week_of_year_sin.iloc[0] == pytest.approx(np.sin(2 * np.pi * expected / 52))
    assert result.week_of_year_cos.iloc[0] == pytest.approx(np.cos(2 * np.pi * expected / 52))
    assert not result.window_obs_complete_28d.any()
    assert not result.window_obs_complete_365d.any()


def test_synthetic_department_climatology_excludes_holdout_and_query_year() -> None:
    cells = pd.DataFrame(
        {
            "cell_id": ["a", "b", "held"],
            "geo_holdout": [False, False, True],
            "department": ["Synthetic"] * 3,
        }
    )
    estimator = TrainingClimatology(cells, [2019, 2020])
    estimator.add(pd.Timestamp("2019-01-07"), cells.assign(y_primary=pd.array([0, 1, 1], "Int8")))
    estimator.add(pd.Timestamp("2020-01-06"), cells.assign(y_primary=pd.array([1, 1, 0], "Int8")))
    np.testing.assert_allclose(estimator.predict_department(pd.Timestamp("2020-01-06")), 0.5)
    np.testing.assert_allclose(estimator.predict_department(pd.Timestamp("2024-01-01")), 0.75)
    assert np.isnan(estimator.predict(pd.Timestamp("2024-01-01"))[-1])


def test_synthetic_department_climatology_weights_each_available_cell_week_equally() -> None:
    cells = pd.DataFrame(
        {
            "cell_id": ["a", "b", "held"],
            "geo_holdout": [False, False, True],
            "department": "Synthetic",
        }
    )
    estimator = TrainingClimatology(cells, [2019, 2020])
    estimator.add(pd.Timestamp("2019-01-07"), cells.assign(y_primary=pd.array([0, 1, 1], "Int8")))
    estimator.add(
        pd.Timestamp("2020-01-06"), cells.assign(y_primary=pd.array([0, None, 1], "Int8"))
    )
    np.testing.assert_allclose(estimator.predict_department(pd.Timestamp("2024-01-01")), 1 / 3)
    # Changing held-out labels must not change either fitted statistic.
    changed = TrainingClimatology(cells, [2019, 2020])
    changed.add(pd.Timestamp("2019-01-07"), cells.assign(y_primary=pd.array([0, 1, 0], "Int8")))
    changed.add(pd.Timestamp("2020-01-06"), cells.assign(y_primary=pd.array([0, None, 0], "Int8")))
    np.testing.assert_array_equal(
        estimator.predict_department(pd.Timestamp("2024-01-01")),
        changed.predict_department(pd.Timestamp("2024-01-01")),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-63.2000000004, 2336), (-63.2000000006, 2335), (-63.1999999996, 2336), (-63.15, 2337)],
)
def test_synthetic_grid_rounds_degrees_before_floor(value: float, expected: int) -> None:
    assert int(coordinate_indices(value, -17.5)[1]) == expected


def test_holdout_matches_independent_hexadecimal_hash_oracle() -> None:
    blocks = [f"b{lon}_{lat}" for lon in range(-70, -57) for lat in range(-23, -9)]
    oracle = sorted(blocks, key=lambda b: (hashlib.sha256(f"20261031:{b}".encode()).hexdigest(), b))
    assert holdout_blocks(blocks, 20261031, 0.2) == set(oracle[: len(blocks) // 5])


def test_synthetic_bundle_fault_does_not_publish_mixed_inputs(tmp_path: Path, monkeypatch) -> None:
    import bolivia_fire.detections as module

    paths = Paths(tmp_path)
    metadata = {
        b"protocol_sha256": b"synthetic",
        b"firms_manifest_sha256": b"synthetic",
        b"outage_snapshot_sha256": b"synthetic",
    }

    def process(paths, protocol, country, sink, **kwargs):
        sink(pd.DataFrame({"synthetic": [1]}), metadata)
        return (
            pd.DataFrame({"date": pd.to_datetime(["2024-09-01"]), "observed": [True]}),
            {},
            metadata,
        )

    monkeypatch.setattr(module, "load_boundary_geometries", lambda p: (box(0, 0, 1, 1), {}))
    monkeypatch.setattr(module, "_process_detections", process)
    module.build_detections(paths, load_protocol())
    pointer = paths.datasets / "interim" / "detection_bundle.json"
    original = pointer.read_bytes()
    monkeypatch.setattr(
        module,
        "write_parquet",
        lambda *args: (_ for _ in ()).throw(
            OSError("synthetic interruption before availability publication")
        ),
    )
    with pytest.raises(OSError, match="synthetic interruption"):
        module.build_detections(paths, load_protocol())
    assert pointer.read_bytes() == original


@pytest.mark.parametrize(
    "key", ["protocol_sha256", "firms_manifest_sha256", "outage_snapshot_sha256"]
)
def test_synthetic_mixed_bundle_provenance_is_rejected(tmp_path: Path, key: str) -> None:
    import json

    import pyarrow.parquet as pq

    from bolivia_fire.detections import resolve_detection_bundle
    from bolivia_fire.firms.storage import sha256_file
    from bolivia_fire.grid import provenance_metadata, write_parquet

    paths = Paths(tmp_path)
    outage_hash = pin_synthetic_outages(paths)
    protocol = load_protocol()
    metadata = provenance_metadata(paths, protocol)
    metadata[b"firms_manifest_sha256"] = b"synthetic manifest"
    metadata[b"outage_snapshot_sha256"] = outage_hash.encode()
    frame = pd.DataFrame({"synthetic": [1]})
    publish_synthetic_bundle(paths, frame, frame, metadata)
    source, days, _ = resolve_detection_bundle(paths, protocol)
    altered = dict(pq.read_schema(days).metadata or {})
    altered[key.encode()] = b"different synthetic snapshot"
    write_parquet(frame, days, altered)
    pointer_path = paths.datasets / "interim" / "detection_bundle.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["files"][days.name] = sha256_file(days)
    pointer_path.write_text(json.dumps(pointer))
    assert source.exists()
    with pytest.raises(ValueError, match="provenance mismatch"):
        resolve_detection_bundle(paths, protocol)


def test_synthetic_nested_layout_does_not_duplicate_last_outage() -> None:
    from bolivia_fire.outages import parse_outages

    html = """<table><tr><td><table><tr><td>2024-001</td><td>January 1, 2024</td>
    <td>01:00:00</td><td>05:00:00</td><td>synthetic outage</td></tr></table></td></tr></table>"""
    assert len(parse_outages(html)) == 1
