from __future__ import annotations

import json
import os
import re
import zipfile
import zlib
from collections import Counter
from pathlib import Path
from typing import BinaryIO

import numpy as np
import pyarrow.parquet as pq

from bolivia_fire.release.build import filesystem_path

PATTERNS = {
    "credential": re.compile(
        rb'(?:api[_-]?key|password|secret|access[_-]?token|authorization)'
        rb'["\x27]?\s*[:=]\s*["\x27](?!<|redacted|none|null)[A-Za-z0-9_+./=-]{12,}'
        rb'|/api/area/(?:csv|json)/(?!<|redacted)[A-Za-z0-9]{16,}'
        rb'|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}', re.I),
    "signed_url": re.compile(
        rb'X-Amz-[A-Za-z0-9_-]+(?:=[^\s"&<>]*)?|\b(?:Signature|token|access_token)=', re.I),
    "absolute_local_path": re.compile(
        rb'(?<![A-Za-z0-9])[A-Z]:[\\/]+[A-Za-z0-9 _.-]+[\\/]+[A-Za-z0-9 _./\\-]+'
        rb'|(?<![A-Za-z0-9.:/])/(?:c/Users|Users|home)/[A-Za-z0-9 _.-]+[\\/]', re.I),
    "account": re.compile(
        rb'"(?:user|username|user_name|user_id|email|account|account_id)"\s*:\s*'
        rb'"(?!<redacted-account>|<redacted>|redacted)[^"\r\n]+"', re.I),
    "email": re.compile(rb'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    "project_identifier": re.compile(
        rb'(?:projects/|["\x27](?:ee_project|earth_engine_project)["\x27]\s*:\s*["\x27])'
        rb'(?!<|public|example|earthengine-public|earthengine-legacy|mapbiomas[-_])'
        rb'[a-z][a-z0-9-]{5,}', re.I),
}


def load_internal_notes(path: Path | None = None) -> re.Pattern[bytes] | None:
    if path is None:
        configured = os.environ.get("BOLIVIA_FIRE_RELEASE_TERMS", "").strip()
        if not configured:
            return None
        path = Path(configured)
    try:
        lines = [line.strip() for line in path.read_bytes().splitlines() if line.strip()]
        if not lines:
            raise ValueError("Internal-notes terms file must contain at least one regex")
        for line in lines:
            re.compile(line, re.I)
        return re.compile(b"|".join(b"(?:" + line + b")" for line in lines), re.I)
    except (OSError, re.error) as exc:
        raise ValueError(
            "Internal-notes terms file is unreadable or contains invalid regexes") from exc


def scan_text(body: bytes, internal_notes: re.Pattern[bytes] | None = None) -> dict[str, int]:
    patterns = dict(PATTERNS)
    if internal_notes is not None:
        patterns["internal_notes"] = internal_notes
    result = {name: len(pattern.findall(body)) for name, pattern in patterns.items()}
    for name in ("EE_PROJECT", "EARTH_ENGINE_PROJECT", "GOOGLE_CLOUD_PROJECT", "USERNAME",
                 "FIRMS_MAP_KEY"):
        value = os.environ.get(name, "")
        if len(value) >= 4 and value.encode().lower() in body.lower():
            result["private_identifier"] = result.get("private_identifier", 0) + 1
    return {name: count for name, count in result.items() if count}


def scan_stream(stream: BinaryIO, internal_notes: re.Pattern[bytes] | None = None) -> Counter:
    result: Counter = Counter()
    patterns = dict(PATTERNS)
    if internal_notes is not None:
        patterns["internal_notes"] = internal_notes
    private = [re.escape(value.encode()) for name in
               ("EE_PROJECT", "EARTH_ENGINE_PROJECT", "GOOGLE_CLOUD_PROJECT", "USERNAME",
                "FIRMS_MAP_KEY")
               if len(value := os.environ.get(name, "")) >= 4]
    if private:
        patterns["private_identifier"] = re.compile(b"|".join(private), re.I)
    tail = b""
    while block := stream.read(1024**2):
        combined = tail + block
        # Retain a boundary window so split tokens are not missed or counted twice.
        stop = max(0, len(combined) - 2048)
        for name, pattern in patterns.items():
            result[name] += sum(m.start() < stop for m in pattern.finditer(combined))
        tail = combined[stop:]
    result.update(scan_text(tail, internal_notes))
    return +result


def array_header(stream: BinaryIO) -> bytes:
    version = np.lib.format.read_magic(stream)
    reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
              else np.lib.format.read_array_header_2_0)
    shape, fortran, dtype = reader(stream)
    if dtype.hasobject:
        raise ValueError("Object array requires separate review")
    return str((shape, fortran, dtype)).encode()


def scan_zip(path: Path, internal_notes: re.Pattern[bytes] | None = None) -> Counter:
    result: Counter = Counter()
    with zipfile.ZipFile(path) as archive:
        result.update(scan_text(archive.comment, internal_notes))
        for item in archive.infolist():
            result.update(scan_text(item.filename.encode() + item.comment, internal_notes))
            with archive.open(item) as stream:
                if item.filename.endswith(".npy"):
                    result.update(scan_text(array_header(stream), internal_notes))
                else:
                    result.update(scan_stream(stream, internal_notes))
    return result


def scan_file(path: Path, internal_notes: re.Pattern[bytes] | None = None) -> dict[str, int]:
    path = filesystem_path(path)
    suffix = path.suffix.lower()
    if path.name.lower() in {"thumbs.db", "desktop.ini", ".ds_store"}:
        return {"os_file": 1}
    if suffix == ".parquet":
        file = pq.ParquetFile(path)
        metadata = file.metadata.metadata or {}
        result: Counter = Counter()
        for key, value in metadata.items():
            result.update(scan_text(key + b"=" + value, internal_notes))
        return dict(result)
    if suffix in {".tif", ".tiff"}:
        import rasterio

        with rasterio.open(path) as raster:
            values = [str(raster.tags()), *(str(raster.tags(i))
                                           for i in range(1, raster.count + 1))]
        return scan_text("\n".join(values).encode(), internal_notes)
    if suffix in {".zip", ".npz", ".pt"}:
        return dict(scan_zip(path, internal_notes))
    if suffix == ".npy":
        with path.open("rb") as stream:
            return scan_text(array_header(stream), internal_notes)
    if suffix == ".joblib":
        body = path.read_bytes()
        if body.startswith(b"x"):
            decoder = zlib.decompressobj()
            body = decoder.decompress(body, 256 * 1024**2)
            if not decoder.eof:
                raise ValueError("Serialized metadata exceeds scan limit")
        # Binary numeric payloads occasionally form path-like bytes by chance.
        strings = re.findall(rb'[\x20-\x7e]{8,}', body)
        return scan_text(b"\n".join(strings), internal_notes)
    if suffix in {".png", ".gif", ".cbm", ".ubj"}:
        strings = re.findall(rb'[\x20-\x7e]{8,}', path.read_bytes())
        return scan_text(b"\n".join(strings), internal_notes)
    with path.open("rb") as stream:
        return dict(scan_stream(stream, internal_notes))


def security_report(files: list[tuple[str, Path]],
                    internal_notes: re.Pattern[bytes] | None = None) -> dict:
    findings = []
    errors = []
    exemptions = []
    for name, path in files:
        try:
            counts = scan_file(path, internal_notes)
            if name.endswith((
                "artifacts/evaluation/amendments/landcover-2026-11-02/extension_selection.json",
                "artifacts/evaluation/extension_archive_attempt_002/extension_execution_report.json",
            )):
                record = json.loads(path.read_text("utf-8"))
                if name.endswith("extension_selection.json"):
                    record = record["preserved_h3"][
                        "artifacts/evaluation/extension_archive_attempt_002/"
                        "extension_execution_report.json"]["record"]
                execution = record.get("execution", {})
                opaque = (isinstance(execution, dict) and len(execution) == 2
                          and all(isinstance(value, str)
                                  and re.fullmatch(r"[a-z][a-z0-9]*_[0-9a-f]{12,64}", value)
                                  for value in execution.values()))
                exempted = (scan_text(json.dumps(execution).encode(), internal_notes)
                            .get("internal_notes", 0) if opaque else 0)
                exempted = min(exempted, counts.get("internal_notes", 0))
                if exempted:
                    counts["internal_notes"] -= exempted
                    if not counts["internal_notes"]:
                        counts.pop("internal_notes")
                    exemptions.append({"path": name, "category": "opaque_execution_identifier",
                                       "counts": {"internal_notes": exempted},
                                       "reason": "Retained for hash-chain integrity"})
            if (name.endswith("-MOD13A2-061-metadata.xml") and "/raw/vegetation/" in name
                    and (count := counts.pop("email", 0))):
                exemptions.append({"path": name, "category": "public_provider_contact",
                                   "counts": {"email": count}})
            if name.endswith(("artifacts/models/comparison/catboost.cbm",
                              "artifacts/models/comparison/catboost_cpu_check.cbm")):
                accepted = {key: counts.pop(key) for key in ("email", "absolute_local_path")
                            if key in counts}
                if accepted:
                    exemptions.append({"path": name, "category": "vendor_library_metadata",
                                       "counts": accepted})
            if counts:
                findings.append({"path": name, "counts": counts})
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            errors.append({"path": name, "error_type": type(exc).__name__})
    return {"passed": not findings and not errors, "files_scanned": len(files),
            "findings": findings, "errors": errors, "values_redacted": True,
            "internal_notes_scan": ("internal-notes scan: configured" if internal_notes is not None
                                    else "internal-notes scan: not configured"),
            "documented_exemptions": exemptions,
            "scope": "Full text, serialized printable strings, scientific file metadata; "
                     "numerical arrays and raster samples excluded"}
