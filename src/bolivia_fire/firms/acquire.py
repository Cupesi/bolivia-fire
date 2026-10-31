from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from bolivia_fire.config import Paths
from bolivia_fire.firms.client import Download, FirmsClient
from bolivia_fire.firms.locking import RunLock
from bolivia_fire.firms.storage import append_manifest, durable_write, read_manifest, sha256_file
from bolivia_fire.firms.validation import BBOX, BBox, validate_csv

LOGGER = logging.getLogger("bolivia_fire.acquire")
PRODUCT = "VIIRS_SNPP_SP"


@dataclass(frozen=True)
class Partition:
    start: date
    day_range: int

    @property
    def filename(self) -> str:
        return f"{PRODUCT}_{self.start}_{self.day_range}d.csv"


def partition_plan(start: date, end: date) -> Iterator[Partition]:
    if start > end:
        raise ValueError("Start date must not be after end date")
    current = start
    while current <= end:
        count = min(5, (end - current).days + 1)
        yield Partition(current, count)
        if (end - current).days < count:
            break
        current += timedelta(days=count)


@dataclass
class AcquisitionSummary:
    downloaded: int = 0
    skipped: int = 0
    recovered: int = 0
    data_requests: int = 0


class Acquisition:
    def __init__(
        self,
        paths: Paths,
        client: FirmsClient,
        *,
        bbox: BBox = BBOX,
        minimum_free_bytes: int = 5 * 1024**3,
        before_promote: Callable[[Path], None] | None = None,
    ) -> None:
        if minimum_free_bytes < 0:
            raise ValueError("Minimum free disk space must be nonnegative")
        self.paths = paths
        self.client = client
        self.bbox = bbox
        self.minimum_free_bytes = minimum_free_bytes
        self.before_promote = before_promote

    def _disk_check(self) -> None:
        free = shutil.disk_usage(self.paths.datasets).free
        if free < self.minimum_free_bytes:
            raise RuntimeError(
                f"Insufficient free disk: {free} bytes; require {self.minimum_free_bytes} bytes",
            )

    def _record(
        self,
        partition: Partition,
        path: Path,
        download: Download,
        *,
        recovered: bool = False,
        quarantine_file: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "product": PRODUCT,
            "bbox": list(self.bbox),
            "start": partition.start.isoformat(),
            "day_range": partition.day_range,
            "retrieval_utc": download.retrieval_utc or None,
            "http_status": download.http_status or None,
            "outcome": download.validation.outcome,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "rows": download.validation.rows,
            "header": download.validation.header,
            "row_validation": download.validation.row_validation,
            "source_versions": download.validation.versions,
            "url": download.url,
            "path": path.relative_to(self.paths.root).as_posix(),
            "recovered": recovered,
        }
        if recovered:
            record["recovery_utc"] = datetime.now(UTC).isoformat()
        if quarantine_file is not None:
            record["recovery_source"] = "quarantine_revalidation"
            record["quarantine_file"] = quarantine_file
        for existing in read_manifest(self.paths.firms_manifest):
            if (
                existing.get("path") == record["path"]
                and existing.get("sha256") == record["sha256"]
            ):
                return existing
        append_manifest(self.paths.firms_manifest, record, self.client.redactor)
        return record

    def revalidate_quarantine(
        self, source: Path, partition: Partition
    ) -> dict[str, Any]:
        source = source.resolve()
        if not source.is_relative_to(self.paths.quarantine.resolve()):
            raise ValueError("Recovery source must be inside the quarantine directory")
        if not 1 <= partition.day_range <= 5:
            raise ValueError("FIRMS day_range must be between 1 and 5")
        with RunLock(self.paths.temporary):
            self._disk_check()
            body = source.read_bytes()
            validation = validate_csv(body, partition.start, partition.day_range, self.bbox)
            if not validation.ok or self.client._key.encode() in body:
                raise ValueError("Quarantine body failed re-validation; source preserved")
            download = Download(
                body, validation,
                self.client.redactor.text(self.client.area_url(
                    partition.start, partition.day_range, self.bbox
                )), 0, "",
            )
            temporary = self.paths.temporary / (partition.filename + ".part")
            if temporary.exists() and temporary.read_bytes() != body:
                raise RuntimeError("Recovery would overwrite a different partial snapshot")
            durable_write(temporary, body)
            canonical = self.paths.firms_raw / str(partition.start.year) / partition.filename
            destination = self._promote(temporary, canonical)
            record = self._record(
                partition, destination, download, recovered=True, quarantine_file=source.name
            )
            if sha256_file(destination) != record["sha256"]:
                raise RuntimeError("Recovered snapshot hash mismatch; quarantine preserved")
            source.unlink()
            LOGGER.info(
                "partition=%s action=quarantine_revalidation outcome=%s rows=%s bytes=%s "
                "data_requests=0", partition.filename, record["outcome"], record["rows"],
                record["bytes"],
            )
            return record

    def _destination(self, canonical: Path, digest: str) -> Path:
        if not canonical.exists() or sha256_file(canonical) == digest:
            return canonical
        return canonical.with_name(f"{canonical.stem}__sha256_{digest}{canonical.suffix}")

    def _promote(self, temporary: Path, canonical: Path) -> Path:
        digest = sha256_file(temporary)
        destination = self._destination(canonical, digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if sha256_file(destination) != digest:
                raise RuntimeError("Snapshot hash collision or modified versioned file")
            return destination
        if self.before_promote is not None:
            self.before_promote(temporary)
        os.replace(temporary, destination)
        return destination

    def _validate_existing(self, path: Path, partition: Partition) -> Download | None:
        body = path.read_bytes()
        validation = validate_csv(body, partition.start, partition.day_range, self.bbox)
        if not validation.ok or self.client._key.encode() in body:
            self.client._quarantine(body, validation.outcome)
            return None
        return Download(
            body,
            validation,
            self.client.redactor.text(
                self.client.area_url(
                    partition.start,
                    partition.day_range,
                    self.bbox,
                )
            ),
            0,
            "",
        )

    def _resume(
        self,
        partition: Partition,
        records: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]] | None:
        canonical = self.paths.firms_raw / str(partition.start.year) / partition.filename
        relevant = [
            record
            for record in records
            if (
                record.get("product") == PRODUCT
                and record.get("start") == str(partition.start)
                and record.get("day_range") == partition.day_range
                and record.get("bbox") == list(self.bbox)
            )
        ]
        for record in reversed(relevant):
            path = (self.paths.root / record["path"]).resolve()
            if not path.is_relative_to(self.paths.firms_raw.resolve()):
                raise RuntimeError("Manifest snapshot path is outside the FIRMS raw directory")
            if path.exists() and sha256_file(path) == record["sha256"]:
                return "skipped", record
        candidates = [
            canonical,
            *sorted(
                canonical.parent.glob(
                    f"{canonical.stem}__sha256_*{canonical.suffix}",
                )
            ),
        ]
        recorded_paths = {record["path"] for record in relevant}
        for path in candidates:
            relative = path.relative_to(self.paths.root).as_posix()
            if not path.exists() or relative in recorded_paths:
                continue
            download = self._validate_existing(path, partition)
            if download is not None:
                record = self._record(partition, path, download, recovered=True)
                records.append(record)
                return "recovered", record
        temporary = self.paths.temporary / (partition.filename + ".part")
        if temporary.exists():
            download = self._validate_existing(temporary, partition)
            if download is not None:
                destination = self._promote(temporary, canonical)
                record = self._record(partition, destination, download, recovered=True)
                records.append(record)
                return "recovered", record
        return None

    def run(
        self,
        start: date,
        end: date,
        *,
        max_partitions: int | None = None,
        force: bool = False,
    ) -> AcquisitionSummary:
        if max_partitions is not None and max_partitions < 1:
            raise ValueError("max_partitions must be positive")
        summary = AcquisitionSummary()
        initial_requests = self.client.data_requests
        with RunLock(self.paths.temporary) as lock:
            if lock.recovered_stale_lock or lock.recovered_interrupted_lock:
                LOGGER.info(
                    "Recovered interrupted acquisition lock; stale=%s", lock.recovered_stale_lock
                )
            records = read_manifest(self.paths.firms_manifest)
            for index, partition in enumerate(partition_plan(start, end)):
                if max_partitions is not None and index >= max_partitions:
                    break
                tick = time.monotonic()
                self._disk_check()
                resumed = None if force else self._resume(partition, records)
                if resumed is not None:
                    action, record = resumed
                    setattr(summary, action, getattr(summary, action) + 1)
                else:
                    try:
                        download = self.client.fetch(
                            partition.start, partition.day_range, self.bbox
                        )
                    except Exception as exc:
                        LOGGER.error(
                            "partition=%s outcome=%s rows=0 elapsed=%.3fs data_requests=%s",
                            partition.filename,
                            getattr(exc, "outcome", "failed"),
                            time.monotonic() - tick,
                            self.client.data_requests - initial_requests,
                        )
                        raise
                    temporary = self.paths.temporary / (partition.filename + ".part")
                    durable_write(temporary, download.body)
                    canonical = (
                        self.paths.firms_raw / str(partition.start.year) / partition.filename
                    )
                    destination = self._promote(temporary, canonical)
                    record = self._record(partition, destination, download)
                    records.append(record)
                    summary.downloaded += 1
                    action = "downloaded"
                LOGGER.info(
                    "partition=%s action=%s outcome=%s rows=%s bytes=%s elapsed=%.3fs",
                    partition.filename,
                    action,
                    record["outcome"],
                    record["rows"],
                    record["bytes"],
                    time.monotonic() - tick,
                )
        summary.data_requests = self.client.data_requests - initial_requests
        LOGGER.info(
            "downloaded=%s skipped=%s recovered=%s data_requests=%s",
            summary.downloaded,
            summary.skipped,
            summary.recovered,
            summary.data_requests,
        )
        return summary
