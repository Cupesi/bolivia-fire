from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from ftplib import FTP
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bolivia_fire.covariates.storage import Budget, atomic_json, read_json
from bolivia_fire.firms.storage import sha256_file


@dataclass(frozen=True)
class AnnualSource:
    url: str
    size: int
    etag: str | None
    last_modified: str | None = None


def same_annual_url(left: str, right: str) -> bool:
    if left == right:
        return True
    first, second = urlsplit(left), urlsplit(right)
    return (first.scheme in {"https", "ftp"} and second.scheme in {"https", "ftp"}
            and {first.hostname, second.hostname} <= {"data.chc.ucsb.edu", "ftp.chc.ucsb.edu"}
            and first.path == second.path
            and first.path.startswith("/products/CHIRPS/v3.0/daily/final/rnl/netcdf/byYear/"))


def prepare_download(
    source: AnnualSource, path: Path, budget: Budget,
) -> tuple[dict[str, Any], int, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = {"url": source.url, "bytes": source.size, "etag": source.etag,
                "last_modified": source.last_modified}
    key = "etag" if source.etag and not source.etag.startswith("W/") else "last_modified"
    state_path = path.with_suffix(path.suffix + ".json")
    offset = path.stat().st_size if path.exists() else 0
    saved = read_json(state_path) if state_path.exists() else {}
    same_url = saved.get("url") == source.url
    cross_protocol = (same_annual_url(saved.get("url", ""), source.url)
                      and bool(source.last_modified)
                      and saved.get("last_modified") == source.last_modified)
    if offset and (not (same_url or cross_protocol) or saved.get("bytes") != source.size):
        raise RuntimeError("Annual CHIRPS partial file identity changed; refusing resume")
    if source.etag and not saved.get("etag") and cross_protocol:
        key = "last_modified"
    validator = identity[key]
    if offset > source.size:
        raise RuntimeError("Annual CHIRPS partial file exceeds verified remote size")
    if offset and (not validator or saved.get(key) != validator):
        budget.check_storage(source.size)
        with path.open("wb"):
            pass
        offset = 0
    budget.check_storage(source.size - offset)
    atomic_json(state_path, identity)
    return identity, offset, validator


def download_ftp(
    source: AnnualSource, path: Path, budget: Budget, client: FTP,
) -> dict[str, Any]:
    identity, offset, _ = prepare_download(source, path, budget)
    transferred = pending_bytes = 0
    tick = time.perf_counter()
    report_tick = time.monotonic()
    if offset < source.size:
        budget.pace_request()
        with path.open("ab" if offset else "wb") as stream:
            def receive(chunk: bytes) -> None:
                nonlocal transferred, pending_bytes, report_tick
                budget.check_storage(len(chunk))
                if stream.tell() + len(chunk) > source.size:
                    raise RuntimeError("Annual CHIRPS FTP download exceeds verified size")
                stream.write(chunk)
                transferred += len(chunk)
                pending_bytes += len(chunk)
                if pending_bytes >= 64 * 1024**2:
                    budget.consume(pending_bytes)
                    pending_bytes = 0
                if time.monotonic() - report_tick >= 30:
                    print(json.dumps({"annual_ftp_download": path.name, "bytes": stream.tell(),
                                      "total": source.size}), flush=True)
                    report_tick = time.monotonic()

            try:
                client.retrbinary("RETR " + urlsplit(source.url).path.rsplit("/", 1)[-1],
                                  receive, blocksize=4 * 1024**2, rest=offset or None)
            finally:
                stream.flush()
                os.fsync(stream.fileno())
                if pending_bytes:
                    budget.consume(pending_bytes)
    if not path.exists() or path.stat().st_size != source.size:
        raise RuntimeError("Annual CHIRPS FTP download is incomplete")
    return {**identity, "sha256": sha256_file(path), "bytes_transferred": transferred,
            "download_seconds": time.perf_counter() - tick}
