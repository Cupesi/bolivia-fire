from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest

from bolivia_fire.config import Paths
from bolivia_fire.firms.acquire import Acquisition, Partition, partition_plan
from bolivia_fire.firms.client import FirmsClient, FirmsError
from bolivia_fire.firms.locking import RunLock
from bolivia_fire.firms.storage import append_manifest, read_manifest
from bolivia_fire.firms.validation import HEADER
from bolivia_fire.redact import Redactor

START = date(2024, 9, 1)
END = date(2024, 9, 5)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2024-09-01", "2024-09-10", [("2024-09-01", 5), ("2024-09-06", 5)]),
        ("2024-02-27", "2024-03-03", [("2024-02-27", 5), ("2024-03-03", 1)]),
        ("2023-12-29", "2024-01-04", [("2023-12-29", 5), ("2024-01-03", 2)]),
        ("2024-09-01", "2024-09-01", [("2024-09-01", 1)]),
        ("9999-12-31", "9999-12-31", [("9999-12-31", 1)]),
    ],
)
def test_partition_plan(start: str, end: str, expected: list[tuple[str, int]]) -> None:
    result = [
        (str(part.start), part.day_range)
        for part in partition_plan(
            date.fromisoformat(start),
            date.fromisoformat(end),
        )
    ]
    assert result == expected


def test_reversed_interval_fails() -> None:
    with pytest.raises(ValueError, match="Start"):
        list(partition_plan(END, START))


def test_quarantine_revalidation_promotes_original_bytes_without_requests(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
) -> None:
    paths = Paths(tmp_path)
    paths.quarantine.mkdir(parents=True)
    body = synthetic_csv().rstrip(b"\n")
    source = paths.quarantine / "firms_truncated_synthetic.txt"
    source.write_bytes(body)
    source.with_suffix(".txt.json").write_text(json.dumps({
        "product": "VIIRS_SNPP_SP", "bbox": [-70, -23, -57, -9],
        "start": "2024-09-01", "day_range": 5,
        "retrieval_utc": "2024-09-06T00:00:00+00:00",
        "sha256": hashlib.sha256(body).hexdigest(),
        "original_sha256": hashlib.sha256(body).hexdigest(),
    }))
    unrelated = paths.quarantine / "firms_truncated_unrelated_synthetic.txt"
    unrelated.write_bytes(b"synthetic invalid response")
    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Offline recovery requested data")
        ),
    ) as client:
        record = Acquisition(paths, client, minimum_free_bytes=0).revalidate_quarantine(
            source, Partition(START, 5)
        )
        assert client.data_requests == 0
        assert not source.exists()
        assert unrelated.exists()
        assert (paths.root / record["path"]).read_bytes() == body
        assert record["recovery_source"] == "quarantine_revalidation"
        assert record["quarantine_file"] == source.name
        assert record["sha256"] == hashlib.sha256(body).hexdigest()
        assert record["retrieval_utc"] == "2024-09-06T00:00:00+00:00"
        assert record["http_status"] is None
        assert record["row_validation"] == {
            "dates_outside_interval": 0,
            "coords_outside_bbox": 0,
            "invalid_acq_time": 0,
            "unexpected_codes": 0,
            "invalid_numeric": 0,
        }
        assert len(read_manifest(paths.firms_manifest)) == 1
        assert Acquisition(paths, client, minimum_free_bytes=0).run(START, END).skipped == 1


@pytest.mark.parametrize("mismatch", [
    None, "product", "bbox", "start", "day_range", "sha256", "original_sha256", "retrieval_utc",
])
def test_synthetic_header_only_quarantine_requires_matching_recorded_identity(
    tmp_path: Path, synthetic_key: str, mismatch: str | None,
) -> None:
    paths = Paths(tmp_path)
    paths.quarantine.mkdir(parents=True)
    source = paths.quarantine / "synthetic_header_only.txt"
    body = (",".join(HEADER) + "\n").encode()
    source.write_bytes(body)
    if mismatch is not None:
        identity = {
            "product": "VIIRS_SNPP_SP", "bbox": [-70, -23, -57, -9],
            "start": "2024-09-01", "day_range": 5,
            "retrieval_utc": "2024-09-06T00:00:00+00:00",
            "sha256": hashlib.sha256(body).hexdigest(),
            "original_sha256": hashlib.sha256(body).hexdigest(),
        }
        identity[mismatch] = {
            "product": "VIIRS_NOAA20_SP", "bbox": [-65, -20, -60, -15],
            "start": "2024-09-02", "day_range": 1, "sha256": "0" * 64,
            "original_sha256": "0" * 64, "retrieval_utc": None,
        }[mismatch]
        source.with_suffix(".txt.json").write_text(json.dumps(identity))
    with FirmsClient(
        synthetic_key, paths.quarantine,
        transport=httpx.MockTransport(lambda request: pytest.fail("Recovery must be offline")),
    ) as client:
        with pytest.raises(ValueError, match="identity|hash"):
            Acquisition(paths, client, minimum_free_bytes=0).revalidate_quarantine(
                source, Partition(START, 5),
            )
        assert source.read_bytes() == body
        assert not paths.firms_manifest.exists()
        assert client.data_requests == 0


def test_synthetic_quarantine_roundtrip_preserves_smaller_request_identity(
    tmp_path: Path, synthetic_key: str,
) -> None:
    paths = Paths(tmp_path)
    body = (",".join(HEADER) + "\n").encode()
    bbox = (-65, -20, -60, -15)
    with FirmsClient(
        synthetic_key, paths.quarantine,
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, content=body, headers={"Content-Length": str(len(body) + 1)},
        )),
    ) as client:
        with pytest.raises(FirmsError):
            client.fetch(START, 1, bbox)
        source = next(paths.quarantine.glob("*.txt"))
        with pytest.raises(ValueError, match="identity"):
            Acquisition(paths, client, minimum_free_bytes=0).revalidate_quarantine(
                source, Partition(START, 5),
            )
        record = Acquisition(paths, client, bbox=bbox, minimum_free_bytes=0).revalidate_quarantine(
            source, Partition(START, 1),
        )
        assert record["outcome"] == "ok_empty"
        assert record["bbox"] == [-65, -20, -60, -15]
        assert record["start"] == "2024-09-01"
        assert record["day_range"] == 1
        assert record["retrieval_utc"]
        assert client.data_requests == 1


@pytest.mark.parametrize("outside_quarantine", [False, True])
def test_quarantine_revalidation_preserves_invalid_or_unowned_files(
    tmp_path: Path, synthetic_key: str, outside_quarantine: bool
) -> None:
    paths = Paths(tmp_path)
    paths.quarantine.mkdir(parents=True)
    source = (tmp_path if outside_quarantine else paths.quarantine) / "synthetic.txt"
    source.write_bytes(b"synthetic incomplete response")
    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Offline recovery requested data")
        ),
    ) as client:
        with pytest.raises(ValueError):
            Acquisition(paths, client, minimum_free_bytes=0).revalidate_quarantine(
                source, Partition(START, 5)
            )
        assert source.read_bytes() == b"synthetic incomplete response"
        assert not paths.firms_manifest.exists()


def test_acquire_resume_has_zero_requests_and_exact_manifest(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
) -> None:
    paths = Paths(tmp_path)
    body = synthetic_csv()
    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    ) as client:
        acquire = Acquisition(paths, client, minimum_free_bytes=0)
        assert acquire.run(START, END).data_requests == 1
        summary = acquire.run(START, END)
        assert summary.data_requests == 0
        assert summary.skipped == 1
        records = read_manifest(paths.firms_manifest)
        assert len(records) == 1
        record = records[0]
        assert record["sha256"] == hashlib.sha256(body).hexdigest()
        assert record["rows"] == 1
        assert record["bytes"] == len(body)
        assert record["http_status"] == 200
        assert record["bbox"] == [-70, -23, -57, -9]
        assert synthetic_key not in paths.firms_manifest.read_text()
        assert "<redacted>" in record["url"]
        assert (tmp_path / record["path"]).read_bytes() == body


def test_crash_after_part_write_is_recovered_without_request(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
) -> None:
    paths = Paths(tmp_path)

    def crash(path: Path) -> None:
        assert path.read_bytes() == synthetic_csv()
        raise RuntimeError("synthetic crash")

    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=synthetic_csv())),
    ) as client:
        with pytest.raises(RuntimeError, match="synthetic crash"):
            Acquisition(paths, client, minimum_free_bytes=0, before_promote=crash).run(START, END)
        assert not paths.firms_manifest.exists()
        assert len(list(paths.temporary.glob("*.csv.part"))) == 1
        summary = Acquisition(paths, client, minimum_free_bytes=0).run(START, END)
        assert summary.data_requests == 0
        assert summary.recovered == 1
        assert read_manifest(paths.firms_manifest)[0]["retrieval_utc"] is None


def test_synthetic_raw_orphan_without_manifest_identity_is_rejected_and_refetched(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
) -> None:
    paths = Paths(tmp_path)
    path = paths.firms_raw / "2024" / "VIIRS_SNPP_SP_2024-09-01_5d.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(synthetic_csv())
    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=synthetic_csv()),
        ),
    ) as client:
        result = Acquisition(paths, client, minimum_free_bytes=0).run(START, END)
        assert result.recovered == 0
        assert result.downloaded == 1
        assert result.data_requests == 1
        assert path.read_bytes() == synthetic_csv()


def test_synthetic_unrecorded_part_is_preserved_and_refetched(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
) -> None:
    paths = Paths(tmp_path)
    paths.temporary.mkdir(parents=True)
    path = paths.temporary / "VIIRS_SNPP_SP_2024-09-01_5d.csv.part"
    path.write_text(f"Invalid MAP_KEY. {synthetic_key}")
    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=synthetic_csv()),
        ),
    ) as client:
        result = Acquisition(paths, client, minimum_free_bytes=0).run(START, END)
        assert result.downloaded == 1
        assert result.data_requests == 1
        quarantine = list(paths.quarantine.glob("*.txt"))
        assert quarantine == []
        assert path.read_text() == f"Invalid MAP_KEY. {synthetic_key}"


def test_changed_refetch_preserves_both_versions(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
) -> None:
    paths = Paths(tmp_path)
    bodies = [synthetic_csv(), synthetic_csv(frp="99.0")]
    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=bodies.pop(0)),
        ),
    ) as client:
        acquire = Acquisition(paths, client, minimum_free_bytes=0)
        acquire.run(START, END)
        acquire.run(START, END, force=True)
        records = read_manifest(paths.firms_manifest)
        assert len(records) == 2
        assert records[0]["path"] != records[1]["path"]
        assert (tmp_path / records[0]["path"]).read_bytes() == synthetic_csv()
        assert (tmp_path / records[1]["path"]).read_bytes() == synthetic_csv(frp="99.0")
        assert "__sha256_" in records[1]["path"]


def test_identical_refetch_keeps_one_manifest_record(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
) -> None:
    paths = Paths(tmp_path)
    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=synthetic_csv()),
        ),
    ) as client:
        acquire = Acquisition(paths, client, minimum_free_bytes=0)
        acquire.run(START, END)
        acquire.run(START, END, force=True)
        assert len(read_manifest(paths.firms_manifest)) == 1


def test_hash_mismatch_requires_refetch(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
) -> None:
    paths = Paths(tmp_path)
    with FirmsClient(
        synthetic_key,
        paths.quarantine,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=synthetic_csv()),
        ),
    ) as client:
        acquire = Acquisition(paths, client, minimum_free_bytes=0)
        acquire.run(START, END)
        record = read_manifest(paths.firms_manifest)[0]
        (tmp_path / record["path"]).write_bytes(synthetic_csv(frp="77.0"))
        assert acquire.run(START, END).data_requests == 1
        assert len(read_manifest(paths.firms_manifest)) == 2


def test_disk_threshold_aborts_before_request(tmp_path: Path, synthetic_key: str) -> None:
    paths = Paths(tmp_path)
    with (
        FirmsClient(
            synthetic_key,
            paths.quarantine,
            transport=httpx.MockTransport(
                lambda request: pytest.fail("Disk check must precede request"),
            ),
        ) as client,
        pytest.raises(RuntimeError, match="Insufficient free disk"),
    ):
        Acquisition(paths, client, minimum_free_bytes=10**30).run(START, END)


def test_stale_metadata_recovers_but_held_lock_cannot_be_stolen(
    tmp_path: Path, synthetic_clock
) -> None:
    metadata = tmp_path / "acquire_firms_owner.json"
    metadata.write_text(json.dumps({"pid": 999999, "heartbeat": 0, "active": True}))
    with RunLock(tmp_path, clock=synthetic_clock.time) as lock:
        assert lock.recovered_stale_lock
        assert json.loads(metadata.read_text())["active"] is True
        with (
            pytest.raises(RuntimeError, match="Another FIRMS"),
            RunLock(tmp_path, clock=synthetic_clock.time),
        ):
            pytest.fail("An OS lock must not be stolen")
    assert json.loads(metadata.read_text())["active"] is False


def test_torn_manifest_is_preserved_and_repaired(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text('{"id": 1}\n{"id":')
    assert read_manifest(path) == [{"id": 1}]
    append_manifest(path, {"id": 2}, Redactor())
    assert read_manifest(path) == [{"id": 1}, {"id": 2}]
    assert len(list(tmp_path.glob("manifest.jsonl.torn-*"))) == 1
