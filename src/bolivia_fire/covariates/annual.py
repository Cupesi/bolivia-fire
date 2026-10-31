from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from datetime import UTC, date, datetime
from email.utils import format_datetime
from ftplib import FTP, all_errors
from pathlib import Path
from typing import Any

import httpx
import netCDF4
import numpy as np
from affine import Affine
from numpy.typing import NDArray
from rasterio.windows import from_bounds

from bolivia_fire.covariates.annual_transport import AnnualSource as AnnualSource
from bolivia_fire.covariates.annual_transport import annual_identity_validator, prepare_download
from bolivia_fire.covariates.annual_transport import download_ftp as download_ftp
from bolivia_fire.covariates.chirps import _array_hash, extract_year
from bolivia_fire.covariates.raster import BBOX, grid_alignment, rainfall_nodata
from bolivia_fire.covariates.storage import Budget, append_jsonl, atomic_json, read_json
from bolivia_fire.firms.storage import sha256_file

ANNUAL_BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/netcdf/byYear/"
FTP_DIRECTORY = "/products/CHIRPS/v3.0/daily/final/rnl/netcdf/byYear"
FTP_BASE = "ftp://ftp.chc.ucsb.edu" + FTP_DIRECTORY + "/"


def annual_source(year: int, client: httpx.Client, budget: Budget) -> AnnualSource | None:
    name = f"chirps-v3.0.rnl.{year}.days_p05.nc"
    budget.pace_request()
    response = client.head(ANNUAL_BASE + name)
    if response.status_code == 404 and year == 2026:
        return None
    response.raise_for_status()
    size = int(response.headers.get("content-length", 0))
    if size <= 0:
        raise RuntimeError("Annual CHIRPS HEAD did not provide a positive file size")
    return AnnualSource(str(response.url), size, response.headers.get("etag"),
                        response.headers.get("last-modified"))


def download_annual(
    source: AnnualSource, path: Path, budget: Budget, client: httpx.Client,
) -> dict[str, Any]:
    identity, offset, validator = prepare_download(source, path, budget)
    transferred = 0
    tick = time.perf_counter()
    for attempt in range(3):
        offset = path.stat().st_size if path.exists() else 0
        if offset == source.size:
            break
        headers = {"Accept-Encoding": "identity"}
        if offset and validator:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = validator
        budget.check_storage(source.size - offset)
        budget.pace_request()
        try:
            with client.stream("GET", source.url, headers=headers) as response:
                response.raise_for_status()
                if response.status_code == 206:
                    expected = f"bytes {offset}-{source.size-1}/{source.size}"
                    if response.headers.get("content-range") != expected:
                        raise RuntimeError("Annual CHIRPS resume Content-Range mismatch")
                elif response.status_code == 200:
                    offset = 0
                    budget.check_storage(source.size)
                else:
                    raise RuntimeError("Unexpected annual CHIRPS download status")
                if int(response.headers.get("content-length", 0)) != source.size - offset:
                    raise RuntimeError("Annual CHIRPS download Content-Length mismatch")
                if (source.etag and response.headers.get("etag")
                        and response.headers["etag"] != source.etag):
                    raise RuntimeError("Annual CHIRPS source changed since HEAD")
                if (not source.etag and source.last_modified
                        and response.headers.get("last-modified")
                        and response.headers["last-modified"] != source.last_modified):
                    raise RuntimeError("Annual CHIRPS source changed since HEAD")
                pending_bytes = 0
                report_tick = time.monotonic()
                with path.open("ab" if offset else "wb") as stream:
                    try:
                        for chunk in response.iter_bytes(chunk_size=4 * 1024**2):
                            budget.check_storage(len(chunk))
                            if stream.tell() + len(chunk) > source.size:
                                raise RuntimeError("Annual CHIRPS download exceeds remote size")
                            stream.write(chunk)
                            pending_bytes += len(chunk)
                            transferred += len(chunk)
                            if pending_bytes >= 64 * 1024**2:
                                budget.consume(pending_bytes)
                                pending_bytes = 0
                            if time.monotonic() - report_tick >= 30:
                                print({"annual_download": path.name, "bytes": stream.tell(),
                                       "total": source.size}, flush=True)
                                report_tick = time.monotonic()
                    finally:
                        stream.flush()
                        os.fsync(stream.fileno())
                        if pending_bytes:
                            budget.consume(pending_bytes)
        except (httpx.TransportError, OSError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
            continue
        if path.stat().st_size != source.size:
            raise RuntimeError("Annual CHIRPS download is incomplete")
        break
    if not path.exists() or path.stat().st_size != source.size:
        raise RuntimeError("Annual CHIRPS download is incomplete")
    return {**identity, "sha256": sha256_file(path), "bytes_transferred": transferred,
            "download_seconds": time.perf_counter() - tick}


class AnnualReader:
    def __init__(self, path: Path, *, bbox: tuple = BBOX) -> None:
        self.dataset = netCDF4.Dataset(path, "r")
        try:
            self._initialize(bbox)
        except BaseException:
            self.dataset.close()
            raise

    def _initialize(self, bbox: tuple) -> None:
        longitude = np.asarray(self.dataset["longitude"][:])
        latitude = np.asarray(self.dataset["latitude"][:])
        if len(longitude) < 2 or len(latitude) < 2:
            raise ValueError("CHIRPS coordinates must contain at least two points")
        if not np.all(np.diff(longitude) > 0):
            raise ValueError("CHIRPS longitudes must be increasing")
        self.flip = bool(latitude[0] < latitude[-1])
        north_to_south = latitude[::-1] if self.flip else latitude
        if not np.all(np.diff(north_to_south) < 0):
            raise ValueError("CHIRPS latitudes must be monotonic")
        lon_index, lon_error = _validate_axis(longitude, -180, 7200)
        lat_index, lat_error = _validate_axis(north_to_south[::-1], -60, 2400)
        north_index = 2400 - lat_index - len(latitude)
        # COGs encode the nominal spacing as float32 and use exact global edges.
        scale = float(np.float32(0.05))
        global_transform = Affine(scale, 0, -180, 0, -scale, 60)
        native = global_transform @ Affine.translation(lon_index, north_index)
        self.grid_validation = {
            "longitude_centres": len(longitude), "latitude_centres": len(latitude),
            "longitude_max_error_deg": lon_error, "latitude_max_error_deg": lat_error,
            "longitude_sha256": hashlib.sha256(longitude.tobytes()).hexdigest(),
            "latitude_sha256": hashlib.sha256(latitude.tobytes()).hexdigest(),
        }
        region = from_bounds(*bbox, native)
        col, row = math.floor(region.col_off + 1e-8), math.floor(region.row_off + 1e-8)
        width = math.ceil(region.col_off + region.width - 1e-8) - col
        height = math.ceil(region.row_off + region.height - 1e-8) - row
        if row < 0 or col < 0 or row + height > len(latitude) or col + width > len(longitude):
            raise ValueError("Annual CHIRPS does not cover the requested native window")
        self.cols = slice(col, col + width)
        self.rows = (slice(len(latitude) - row - height, len(latitude) - row) if self.flip
                     else slice(row, row + height))
        self.transform = list(native @ Affine.translation(col, row))[:6]
        self.variable = self.dataset["precip"]
        if self.variable.dimensions != ("time", "latitude", "longitude"):
            raise ValueError("Annual CHIRPS precipitation dimensions changed")
        self.variable.set_auto_maskandscale(False)
        self.variable.set_var_chunk_cache(size=128 * 1024**2, nelems=1009, preemption=0.75)
        time_axis = self.dataset["time"]
        stamps = netCDF4.num2date(time_axis[:], time_axis.units,
                                 calendar=getattr(time_axis, "calendar", "standard"))
        days = [date(stamp.year, stamp.month, stamp.day) for stamp in stamps]
        if len(set(days)) != len(days) or days != sorted(days):
            raise ValueError("Annual CHIRPS dates must be unique and increasing")
        self.indices = {day: index for index, day in enumerate(days)}

    def __enter__(self) -> AnnualReader:
        return self

    def __exit__(self, *args: Any) -> None:
        self.dataset.close()

    def read(self, day: date) -> tuple[NDArray, dict[str, Any]]:
        if day not in self.indices:
            raise ValueError(f"Annual CHIRPS date missing: {day}")
        raw = self.variable[self.indices[day], self.rows, self.cols]
        if self.flip:
            raw = raw[::-1]
        raw = np.asarray(raw, dtype=np.float32).copy()
        for attribute in ("_FillValue", "missing_value"):
            for missing in np.asarray(getattr(self.variable, attribute, [])).ravel():
                raw[raw == missing] = np.nan
        values = rainfall_nodata(raw, None)
        return values, {"transform": self.transform, "crs": "EPSG:4326",
                        "nodata_count": int(np.isnan(values).sum()),
                        "alignment": grid_alignment(Affine(*self.transform))}


def _validate_axis(values: NDArray, origin: float, count: int) -> tuple[int, float]:
    expected = origin + (np.arange(count, dtype=np.float64) + 0.5) * 0.05
    first = int(np.argmin(np.abs(expected - float(values[0]))))
    wanted = expected[first:first + len(values)]
    if len(wanted) != len(values):
        raise ValueError("Annual CHIRPS coordinates extend beyond the native grid")
    error = np.abs(values.astype(np.float64) - wanted)
    # Provider coordinates use float32 endpoint subtraction, division and addition.
    low = np.float32(origin + 0.025)
    high = np.float32(origin + count * 0.05 - 0.025)
    step = (high - low) / np.float32(count - 1)
    encoded = low + np.arange(count, dtype=np.float32) * step
    candidates = (wanted, encoded[first:first + len(values)].astype(np.float64))
    for candidate in candidates:
        tolerance = (np.abs(np.spacing(candidate.astype(values.dtype))).astype(np.float64) / 2
                     + 1e-10)
        if np.isfinite(values).all() and np.all(np.abs(values - candidate) <= tolerance):
            return first, float(error.max(initial=0))
    raise ValueError("Annual CHIRPS coordinate centres differ from the native grid")


def _cached_windows(year: int, root: Path) -> dict[str, NDArray]:
    directory = root / "chirps_daily" / f"year={year}"
    cached = {}
    complete_path = directory / "complete.json"
    if complete_path.exists():
        complete = read_json(complete_path)
        output = directory / "part.npz"
        if sha256_file(output) != complete["sha256"]:
            raise RuntimeError("Completed CHIRPS year checksum mismatch")
        with np.load(output) as archive:
            cached.update(zip(archive["dates"].tolist(), archive["rain"], strict=True))
    for path in sorted(directory.glob("????-??-??.npz")):
        with np.load(path) as archive:
            cached[path.stem] = archive["rain"]
    manifest = root / "chirps_manifest.jsonl"
    records = {}
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            records[record["date"]] = record
    for day, values in cached.items():
        if day not in records or _array_hash(values) != records[day]["window_sha256"]:
            raise RuntimeError("Cached CHIRPS window checksum mismatch")
    return cached


def extract_annual_file(
    year: int, path: Path, root: Path, *, source: dict[str, Any],
    budget: Budget | None = None, start: date | None = None, end: date | None = None,
    bbox: tuple = BBOX,
) -> dict[str, Any]:
    budget = budget or Budget(root)
    tick = time.perf_counter()
    cached = _cached_windows(year, root)
    max_diff = 0.0
    window_hashes = {}
    with AnnualReader(path, bbox=bbox) as reader:
        grid_validation = reader.grid_validation
        for day, previous in sorted(cached.items()):
            current, _ = reader.read(date.fromisoformat(day))
            if (previous.shape != current.shape
                    or not np.array_equal(np.isnan(previous), np.isnan(current))):
                raise RuntimeError(f"Annual CHIRPS consistency mismatch on {day}: shape/nodata")
            valid = np.isfinite(previous)
            difference = float(np.max(np.abs(previous[valid] - current[valid]), initial=0))
            tolerance = np.maximum(np.abs(np.spacing(previous[valid])),
                                   np.abs(np.spacing(current[valid])))
            if np.any(np.abs(previous[valid] - current[valid]) > tolerance):
                raise RuntimeError(f"Annual CHIRPS consistency mismatch on {day}: {difference}")
            max_diff = max(max_diff, difference)

        def read_day(day: date, budget: Budget) -> tuple[NDArray, dict[str, Any]]:
            values, record = reader.read(day)
            return values, {**record, "url": source["url"], "http_size": source.get("bytes"),
                            "annual_sha256": source.get("sha256"), "encoding": "NetCDF"}

        for day in reader.indices:
            if (start or date(year, 1, 1)) <= day <= (end or date(year, 12, 31)):
                values, _ = reader.read(day)
                window_hashes[day.isoformat()] = _array_hash(values)
        result = extract_year(year, root, reader=read_day, budget=budget, start=start, end=end)
    result = {**result, "annual_source": source, "consistency_days": len(cached),
              "max_abs_diff": max_diff, "window_sha256": window_hashes,
              "native_grid_validation": grid_validation,
              "extraction_seconds": time.perf_counter() - tick}
    append_jsonl(root / "chirps_annual_manifest.jsonl", result)
    path.unlink()
    path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)
    return result


def extract_annual_range(
    start_year: int, end_year: int, root: Path, *, transport: str = "https",
) -> dict[str, Any]:
    if not 2012 <= start_year <= end_year <= 2026:
        raise ValueError("Annual CHIRPS years must stay within 2012 through 2026")
    if transport == "ftp":
        return _extract_ftp_range(start_year, end_year, root)
    if transport != "https":
        raise ValueError("Annual CHIRPS transport must be https or ftp")
    try:
        return _extract_https_range(start_year, end_year, root)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 429:
            raise
        budget = Budget(root)
        budget.record_throttle(exc.response.headers.get("retry-after"))
        match = re.search(r"chirps-v3\.0\.rnl\.(\d{4})\.days_p05\.nc", str(exc.request.url))
        first = int(match[1]) if match else start_year
        progress = root / "annual_progress.json"
        prefix = ([record for record in read_json(progress)["years"]
                   if start_year <= record["year"] < first] if progress.exists() else [])
        print(json.dumps({"transport": "ftp", "reason": "HTTP 429", "start_year": first}),
              flush=True)
        return _extract_ftp_range(first, end_year, root, prefix=prefix)


def _extract_https_range(start_year: int, end_year: int, root: Path) -> dict[str, Any]:
    budget = Budget(root)
    temporary_root = root.parents[1] / ".tmp"
    result: dict[str, Any] = {"years": []}
    with httpx.Client(timeout=httpx.Timeout(120, connect=60), follow_redirects=True) as client:
        budget.pace_request()
        listing = client.get(ANNUAL_BASE)
        listing.raise_for_status()
        published = set(re.findall(r'href="(chirps-v3\.0\.rnl\.\d{4}\.days_p05\.nc)"',
                                   listing.text))
        for year in range(start_year, end_year + 1):
            tick = time.perf_counter()
            name = f"chirps-v3.0.rnl.{year}.days_p05.nc"
            end = date(2026, 1, 4) if year == 2026 else date(year, 12, 31)
            if name not in published and year != 2026:
                raise RuntimeError(f"Annual CHIRPS file absent from HTTPS listing: {name}")
            source = annual_source(year, client, budget) if name in published else None
            if source is None:
                record = extract_year(year, root, budget=budget, end=end)
                record["annual_fallback"] = "COG: 2026 annual file not published"
            else:
                previous = completed_annual(year, root, source, date(year, 1, 1), end)
                if previous is not None:
                    result["years"].append({**previous, "reused": True})
                    continue
                path = temporary_root / (name + ".part")
                downloaded = download_annual(source, path, budget, client)
                record = extract_annual_file(year, path, root, source=downloaded,
                                             budget=budget, end=end)
            record["runtime_seconds"] = time.perf_counter() - tick
            result["years"].append(record)
            atomic_json(root / "annual_progress.json", result)
            print(json.dumps({key: value for key, value in record.items()
                              if key != "window_sha256"}), flush=True)
    return result


def _extract_ftp_range(
    start_year: int, end_year: int, root: Path, *, prefix: list[dict] | None = None,
) -> dict[str, Any]:
    budget = Budget(root)
    temporary_root = root.parents[1] / ".tmp"
    result: dict[str, Any] = {"years": list(prefix or [])}
    with FTP("ftp.chc.ucsb.edu", timeout=120) as client:
        client.login()
        client.cwd(FTP_DIRECTORY)
        published = set(client.nlst())
    for year in range(start_year, end_year + 1):
        tick = time.perf_counter()
        name = f"chirps-v3.0.rnl.{year}.days_p05.nc"
        end = date(2026, 1, 4) if year == 2026 else date(year, 12, 31)
        if name not in published:
            if year != 2026:
                raise RuntimeError(f"Annual CHIRPS file absent from FTP listing: {name}")
            record = extract_year(year, root, budget=budget, end=end)
            record["annual_fallback"] = "COG: 2026 annual file not published"
        else:
            for attempt in range(3):
                try:
                    with FTP("ftp.chc.ucsb.edu", timeout=120) as client:
                        client.login()
                        client.cwd(FTP_DIRECTORY)
                        client.voidcmd("TYPE I")
                        size = client.size(name)
                        if size is None or size <= 0:
                            raise RuntimeError("FTP SIZE did not provide a positive file size")
                        modified = client.sendcmd("MDTM " + name).split()[-1]
                        stamp = datetime.strptime(modified, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                        source = AnnualSource(FTP_BASE + name, size, None,
                                              format_datetime(stamp, usegmt=True))
                        previous = completed_annual(year, root, source, date(year, 1, 1), end)
                        if previous is not None:
                            record = {**previous, "reused": True}
                        else:
                            path = temporary_root / (name + ".part")
                            downloaded = download_ftp(source, path, budget, client)
                            record = extract_annual_file(year, path, root, source=downloaded,
                                                         budget=budget, end=end)
                    break
                except all_errors:
                    if attempt == 2:
                        raise
                    time.sleep(5 * (attempt + 1))
        if not record.get("reused"):
            record["runtime_seconds"] = time.perf_counter() - tick
        result["years"].append(record)
        atomic_json(root / "annual_progress.json", result)
        print(json.dumps({key: value for key, value in record.items()
                          if key != "window_sha256"}), flush=True)
    return result


def completed_annual(
    year: int, root: Path, source: AnnualSource, start: date, end: date,
) -> dict[str, Any] | None:
    manifest = root / "chirps_annual_manifest.jsonl"
    complete_path = root / "chirps_daily" / f"year={year}" / "complete.json"
    if not manifest.exists() or not complete_path.exists():
        return None
    complete = read_json(complete_path)
    if complete["start"] > start.isoformat() or complete["end"] < end.isoformat():
        return None
    for line in reversed(manifest.read_text(encoding="utf-8").splitlines()):
        record = json.loads(line)
        identity = record["annual_source"]
        if (record["year"] == year and annual_identity_validator(source, identity)
                and record["sha256"] == complete["sha256"]):
            if sha256_file(complete_path.parent / "part.npz") != complete["sha256"]:
                raise RuntimeError("Completed CHIRPS year checksum mismatch")
            return record
    return None
