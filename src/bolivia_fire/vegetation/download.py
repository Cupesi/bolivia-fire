from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from bolivia_fire.vegetation.client import AppEEARSClient, AppEEARSError, TransientError
from bolivia_fire.vegetation.manifest import utc_now


class IncompleteBundleError(AppEEARSError):
    pass


class ChecksumError(AppEEARSError):
    pass


class Budget:
    def __init__(self, root: Path, minimum_free_gb: float = 55) -> None:
        self.root, self.minimum_free = root, int(minimum_free_gb * 1024**3)
        root.mkdir(parents=True, exist_ok=True)
        self.transferred = 0
        self._lock = threading.Lock()

    def check(self, incoming: int = 0) -> None:
        if shutil.disk_usage(self.root).free - incoming < self.minimum_free:
            raise OSError("Vegetation free-space floor would be crossed; acquisition stopped")

    def write(self, stream: Any, chunk: bytes) -> None:
        with self._lock:
            self.check(len(chunk))
            stream.write(chunk)
            self.transferred += len(chunk)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_bundle(
    bundle: dict[str, Any],
    layers: list[str],
    *,
    expected_dates: set[str] | None = None,
) -> list[dict[str, Any]]:
    files = bundle.get("files")
    if not isinstance(files, list) or not files:
        raise IncompleteBundleError("AppEEARS bundle contains no files")
    names, identifiers = set(), set()
    for item in files:
        try:
            name, file_id = item["file_name"], item["file_id"]
            size, checksum = item["file_size"], item["sha256"]
            path = PurePosixPath(name)
            valid = (
                isinstance(name, str)
                and name
                and not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in name
                and ":" not in name
                and isinstance(file_id, str)
                and re.fullmatch(r"[\w-]+", file_id)
                and isinstance(size, int)
                and not isinstance(size, bool)
                and size > 0
                and isinstance(checksum, str)
                and re.fullmatch(r"[a-fA-F0-9]{64}", checksum)
                and name not in names
                and file_id not in identifiers
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise IncompleteBundleError("AppEEARS bundle has invalid file identity or checksum")
        names.add(name)
        identifiers.add(file_id)
    date_sets = []
    for layer in layers:
        matched = [
            name
            for name in names
            if layer.lstrip("_") in name and name.lower().endswith((".tif", ".tiff"))
        ]
        if not matched:
            raise IncompleteBundleError("AppEEARS bundle is missing a requested raster layer")
        dates = {match[1] for name in matched if (match := re.search(r"_doy(\d{7})", name))}
        if expected_dates and not expected_dates.issubset(dates):
            raise IncompleteBundleError("AppEEARS bundle is missing an expected composite date")
        if dates:
            date_sets.append(dates)
    if date_sets and any(dates != date_sets[0] for dates in date_sets):
        raise IncompleteBundleError(
            "AppEEARS bundle has inconsistent composite dates across layers"
        )
    return files


def download_file(
    client: AppEEARSClient,
    task_id: str,
    item: dict[str, Any],
    root: Path,
    budget: Budget,
) -> dict[str, Any]:
    destination = (root / item["file_name"]).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise IncompleteBundleError("Bundle file escapes output directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    size, expected = item["file_size"], item["sha256"].lower()
    partial = destination.with_name(destination.name + ".part")
    transferred = 0
    if destination.exists():
        if destination.stat().st_size != size or sha256_file(destination) != expected:
            raise ChecksumError("Existing AppEEARS file does not match bundle identity")
    else:
        for attempt in range(client.retries + 1):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > size:
                raise IncompleteBundleError("AppEEARS partial file is larger than bundle file")
            if offset == size:
                break
            budget.check(size - offset)
            response = client.stream_file(task_id, item["file_id"], offset)
            try:
                if response.status_code == 206:
                    content_range = response.headers.get("Content-Range", "")
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                    if (
                        not match
                        or int(match[1]) != offset
                        or int(match[3]) != size
                        or int(match[2]) != size - 1
                    ):
                        raise IncompleteBundleError("AppEEARS download Content-Range mismatch")
                elif response.status_code == 200:
                    offset = 0
                else:
                    raise IncompleteBundleError("AppEEARS download did not return file content")
                mode = "ab" if offset else "wb"
                with partial.open(mode) as stream:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if stream.tell() + len(chunk) > size:
                            raise IncompleteBundleError("AppEEARS download exceeds advertised size")
                        budget.write(stream, chunk)
                        transferred += len(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if partial.stat().st_size == size:
                    break
            except httpx.HTTPError:
                if attempt >= client.retries:
                    raise TransientError(
                        "AppEEARS file transfer interrupted; partial preserved"
                    ) from None
            finally:
                response.close()
            client._sleep(client._delay(None, attempt))
        if not partial.exists() or partial.stat().st_size != size:
            raise IncompleteBundleError("AppEEARS download is incomplete; partial preserved")
        if sha256_file(partial) != expected:
            raise ChecksumError("AppEEARS SHA-256 mismatch; partial preserved")
        partial.replace(destination)
    return {**item, "retrieved_utc": utc_now(), "bytes_transferred": transferred}
