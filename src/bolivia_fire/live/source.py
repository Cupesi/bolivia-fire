from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from shapely.geometry.base import BaseGeometry
from sklearn.neighbors import BallTree

from bolivia_fire.detections import SOURCE_DTYPES, _parse_source
from bolivia_fire.firms.client import Download, FirmsClient, FirmsError
from bolivia_fire.firms.storage import durable_write, sha256_file
from bolivia_fire.firms.validation import BBOX, HEADER, BBox, Outcome, Validation, validate_csv
from bolivia_fire.live.files import read_json, write_json

SP = "VIIRS_SNPP_SP"
NRT = "VIIRS_SNPP_NRT"
NRT_HEADER = HEADER[:-1]
PROXY = (
    "NRT has no type field: nominal/high-confidence detections are retained only outside "
    "375 m great-circle buffers around SP type 1/2 locations observed during "
    "2012-01-20 through 2026-07-31. This static-source mask approximates SP type=0; "
    "unseen static sources and non-vegetation detections can remain."
)


def validate_nrt(body: bytes, start: date, length: int, bbox: BBox, status: int) -> Validation:
    try:
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig")), strict=True))
    except (UnicodeDecodeError, csv.Error):
        return Validation(Outcome.TRUNCATED)
    if not rows or tuple(rows[0]) != NRT_HEADER:
        existing = validate_csv(body, start, length, bbox, status)
        return existing if not existing.ok else Validation(Outcome.SCHEMA_DRIFT)
    if any(len(row) != len(NRT_HEADER) for row in rows[1:]):
        return Validation(Outcome.TRUNCATED)
    versions = sorted({row[10] for row in rows[1:]})
    if any(version != "2.0NRT" for version in versions):
        return Validation(Outcome.SCHEMA_DRIFT, versions=versions)
    # The extra field is used only to reuse SP's measurement/code validation.
    # Actual vegetation-proxy assignment happens after the static mask below.
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows([*row, "0"] for row in rows[1:])
    result = validate_csv(stream.getvalue().encode(), start, length, bbox, status)
    result.header = list(NRT_HEADER)
    return result


class LiveClient(FirmsClient):
    def fetch_product(self, product: str, start: date, length: int, bbox: BBox = BBOX) -> Download:
        if product == SP:
            return self.fetch(start, length, bbox)
        if product != NRT:
            raise ValueError("Live acquisition supports only VIIRS S-NPP SP and NRT")
        if self.quota is not None:
            self.quota.reconcile(self.status_uncoordinated)
        url = self.area_url(start, length, bbox).replace(f"/{SP}/", f"/{NRT}/")
        identity = {
            "product": product,
            "bbox": list(bbox),
            "start": str(start),
            "day_range": length,
        }
        response = self._request(url, length * self.transactions_per_day, identity=identity)
        retrieval = datetime.fromtimestamp(self.clock(), UTC).isoformat()
        validation = validate_nrt(response.content, start, length, bbox, response.status_code)
        if self._key.encode() in response.content:
            validation = Validation(Outcome.SCHEMA_DRIFT)
        if not validation.ok:
            self._quarantine(
                response.content, validation.outcome, {**identity, "retrieval_utc": retrieval}
            )
            raise FirmsError(
                validation.outcome, f"FIRMS {validation.outcome}: {self.redactor.text(url)}"
            )
        return Download(
            response.content, validation, self.redactor.text(url), response.status_code, retrieval
        )


class StaticSourceMask:
    def __init__(self, locations: pd.DataFrame) -> None:
        self.locations = locations[["latitude", "longitude"]].astype(float).drop_duplicates()
        self.tree = (
            BallTree(np.radians(self.locations.to_numpy()), metric="haversine")
            if len(self.locations)
            else None
        )

    def excluded(self, frame: pd.DataFrame) -> NDArray[np.bool_]:
        if self.tree is None or frame.empty:
            return np.zeros(len(frame), dtype=bool)
        distance, _ = self.tree.query(
            np.radians(frame[["latitude", "longitude"]].to_numpy(dtype=float)), k=1
        )
        return distance[:, 0] <= 375 / 6_371_008.8


def parse_download(
    body: bytes, product: str, country: BaseGeometry, mask: StaticSourceMask | None = None
) -> tuple[pd.DataFrame, int]:
    frame = pd.read_csv(io.BytesIO(body), dtype="string", keep_default_na=False).drop_duplicates()
    removed = 0
    if product == NRT:
        if mask is None:
            raise ValueError("NRT parsing requires the historical static-source mask")
        frame = frame[frame.confidence.isin(["n", "h"])].copy()
        excluded = mask.excluded(frame)
        removed = int(excluded.sum())
        frame = frame[~excluded].copy()
        frame["type"] = "0"
    return _parse_source(frame.astype(SOURCE_DTYPES), country), removed


def cached_download(
    client: LiveClient, cache: Path, product: str, start: date, length: int, bbox: BBox
) -> tuple[bytes, dict[str, Any]]:
    path = cache / "raw" / product / f"{start}_{length}d.csv"
    metadata_path = path.with_suffix(".json")
    identity = {"product": product, "start": str(start), "day_range": length, "bbox": list(bbox)}
    if path.exists() and metadata_path.exists():
        metadata = read_json(metadata_path)
        if any(metadata.get(key) != value for key, value in identity.items()):
            raise ValueError("Cached live request identity mismatch")
        if sha256_file(path) != metadata["sha256"]:
            raise ValueError("Cached live response hash mismatch")
        body = path.read_bytes()
        validation = (
            validate_nrt(body, start, length, bbox, 200)
            if product == NRT
            else validate_csv(body, start, length, bbox)
        )
        if not validation.ok:
            raise ValueError("Cached live response failed validation")
        return body, metadata
    download = client.fetch_product(product, start, length, bbox)
    durable_write(path, download.body)
    metadata = {
        **identity,
        "sha256": sha256_file(path),
        "url": download.url,
        "retrieval_utc": download.retrieval_utc,
        "versions": download.validation.versions,
        "rows": download.validation.rows,
    }
    write_json(metadata_path, metadata)
    return download.body, metadata
