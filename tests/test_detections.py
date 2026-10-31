import hashlib
import json
from datetime import date

import pandas as pd
import pytest
from shapely.geometry import box

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.detections import read_detections


def synthetic_manifest(tmp_path, synthetic_csv):
    paths = Paths(tmp_path)
    path = paths.firms_raw / "2024" / "synthetic.csv"
    path.parent.mkdir(parents=True)
    first = synthetic_csv(acq_date="2024-02-29", acq_time="5")
    second = synthetic_csv(acq_date="2024-02-29", acq_time="2359", frp="13.5")
    outside = synthetic_csv(acq_date="2024-03-01", latitude="-20", acq_time="0")
    body = first + first.split(b"\n", 1)[1] + second.split(b"\n", 1)[1]
    body += outside.split(b"\n", 1)[1]
    path.write_bytes(body)
    paths.manifests.mkdir(parents=True)
    record = {
        "product": "VIIRS_SNPP_SP",
        "path": path.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "start": "2024-02-29",
        "day_range": 2,
        "outcome": "ok_rows",
        "rows": 4,
    }
    paths.firms_manifest.write_text(json.dumps(record) + "\n")
    return paths, path


def test_synthetic_hhmm_leap_day_dedup_and_country(tmp_path, synthetic_csv):
    paths, _ = synthetic_manifest(tmp_path, synthetic_csv)
    data, days, stats = read_detections(paths, load_protocol(), box(-64, -18, -63, -17))
    assert stats["duplicates"] == 1
    assert len(data) == 3
    assert data.acq_time.tolist() == ["0005", "2359", "0000"]
    assert data.source_line.tolist() == [1, 3, 4]
    assert data.in_bolivia.tolist() == [True, True, False]
    assert str(data.acq_datetime.iloc[0]) == "2024-02-29 00:05:00+00:00"
    assert days.loc[days.date == pd.Timestamp("2024-02-29"), "bbox_detection_count"].item() == 2
    assert days.loc[days.date == pd.Timestamp("2024-03-01"), "acquired"].item()
    assert not days.loc[days.date == pd.Timestamp("2024-03-02"), "acquired"].item()


def test_synthetic_hash_mismatch_is_fatal(tmp_path, synthetic_csv):
    paths, path = synthetic_manifest(tmp_path, synthetic_csv)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash"):
        read_detections(paths, load_protocol(), box(-64, -18, -63, -17))


@pytest.mark.parametrize("time", ["2400", "1260", "", "-1"])
def test_synthetic_invalid_times_rejected(tmp_path, synthetic_csv, time):
    paths, path = synthetic_manifest(tmp_path, synthetic_csv)
    body = synthetic_csv(acq_date="2024-02-29", acq_time=time)
    path.write_bytes(body)
    record = json.loads(paths.firms_manifest.read_text())
    record.update(sha256=hashlib.sha256(body).hexdigest(), rows=1)
    paths.firms_manifest.write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError):
        read_detections(paths, load_protocol(), box(-64, -18, -63, -17))


def test_synthetic_year_transition_and_boundary_inclusion(tmp_path, synthetic_csv):
    paths, path = synthetic_manifest(tmp_path, synthetic_csv)
    body = synthetic_csv(acq_date="2023-12-31", acq_time="2359", longitude="-63")
    body += synthetic_csv(acq_date="2024-01-01", acq_time="0").split(b"\n", 1)[1]
    path.write_bytes(body)
    record = json.loads(paths.firms_manifest.read_text())
    record.update(sha256=hashlib.sha256(body).hexdigest(), rows=2, start=str(date(2023, 12, 31)))
    paths.firms_manifest.write_text(json.dumps(record) + "\n")
    data, _, _ = read_detections(paths, load_protocol(), box(-64, -18, -63, -17))
    assert data.in_bolivia.all()
    assert str(data.acq_datetime.iloc[1]) == "2024-01-01 00:00:00+00:00"


def test_synthetic_manifest_snapshot_is_recorded_and_covered_partitions_skipped(
    tmp_path, synthetic_csv
):
    paths, path = synthetic_manifest(tmp_path, synthetic_csv)
    first = json.loads(paths.firms_manifest.read_text())
    other = path.with_name("synthetic_overlap.csv")
    other.write_bytes(path.read_bytes())
    second = {**first, "path": other.relative_to(tmp_path).as_posix()}
    manifest = (json.dumps(first) + "\n" + json.dumps(second) + "\n").encode()
    paths.firms_manifest.write_bytes(manifest)
    data, _, stats = read_detections(paths, load_protocol(), box(-64, -18, -63, -17))
    assert stats["firms_manifest_sha256"] == hashlib.sha256(manifest).hexdigest()
    assert stats["source_rows"] == 4
    assert stats["duplicates"] == 1
    assert stats["overlap_partitions_skipped"] == 1
    assert data.source_file.eq(first["path"]).all()


def test_synthetic_larger_partition_wins_even_when_smaller_recorded_first(tmp_path, synthetic_csv):
    paths, path = synthetic_manifest(tmp_path, synthetic_csv)
    larger = json.loads(paths.firms_manifest.read_text())
    smaller_path = path.with_name("synthetic_1d.csv")
    body = synthetic_csv(acq_date="2024-02-29", frp="999")
    smaller_path.write_bytes(body)
    smaller = {
        **larger,
        "day_range": 1,
        "rows": 1,
        "path": smaller_path.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    paths.firms_manifest.write_text(json.dumps(smaller) + "\n" + json.dumps(larger) + "\n")
    data, _, stats = read_detections(paths, load_protocol(), box(-64, -18, -63, -17))
    assert data.source_file.eq(larger["path"]).all()
    assert 999 not in data.frp.tolist()
    assert stats["overlap_partitions_skipped"] == 1


def test_synthetic_partial_overlap_keeps_uncovered_day_and_original_source_line(
    tmp_path, synthetic_csv
):
    paths, path = synthetic_manifest(tmp_path, synthetic_csv)
    first = json.loads(paths.firms_manifest.read_text())
    other = path.with_name("synthetic_partial.csv")
    body = synthetic_csv(acq_date="2024-03-01", frp="999")
    body += synthetic_csv(acq_date="2024-03-02", frp="42").split(b"\n", 1)[1]
    other.write_bytes(body)
    second = {
        **first,
        "start": "2024-03-01",
        "rows": 2,
        "path": other.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    paths.firms_manifest.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    data, days, stats = read_detections(paths, load_protocol(), box(-64, -18, -63, -17))
    assert data.frp.tolist() == [12.5, 13.5, 12.5, 42]
    assert data.source_line.tolist() == [1, 3, 4, 2]
    assert stats["overlap_partitions_skipped"] == 0
    assert stats["overlap_days_skipped"] == 1
    assert days.loc[days.date.between("2024-02-29", "2024-03-02"), "acquired"].all()


def test_synthetic_successful_empty_partition_is_acquired(tmp_path, synthetic_csv):
    paths, path = synthetic_manifest(tmp_path, synthetic_csv)
    body = synthetic_csv().split(b"\n", 1)[0] + b"\n"
    path.write_bytes(body)
    record = json.loads(paths.firms_manifest.read_text())
    record.update(sha256=hashlib.sha256(body).hexdigest(), rows=0, outcome="ok_empty")
    paths.firms_manifest.write_text(json.dumps(record) + "\n")
    data, days, stats = read_detections(paths, load_protocol(), box(-64, -18, -63, -17))
    assert data.empty
    assert stats["rows"] == 0
    assert stats["acquired_days"] == 2
    assert days.loc[days.date.eq("2024-02-29"), "acquired"].item()
    assert days.bbox_detection_count.sum() == 0
