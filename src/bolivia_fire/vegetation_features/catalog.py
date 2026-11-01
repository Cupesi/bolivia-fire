from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import shapely
from shapely.geometry import shape

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.grid import load_boundary_geometries

ROLES = {
    "ndvi": "NDVI",
    "evi": "EVI",
    "vi_quality": "VI_Quality",
    "pixel_reliability": "pixel_reliability",
    "composite_doy": "composite_day_of_the_year",
}


def validate_statistics_start(
    name: str, role: str, start: str, statistics_dates: dict[str, str]
) -> None:
    if role in {"vi_quality", "pixel_reliability"}:
        valid = start in statistics_dates.values()
    else:
        valid = statistics_dates.get(Path(name).stem.replace(".", "_")) == start
    if not valid:
        raise ValueError("Raster start disagrees with AppEEARS statistics metadata")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Composite:
    start: pd.Timestamp
    end: pd.Timestamp
    files: dict[str, Path] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    confirmation_ok: bool = False

    @property
    def key(self) -> str:
        return self.start.strftime("%Y%j")


def discover(root: Path) -> tuple[list[Composite], dict[str, Any]]:
    manifest_dir = root / "datasets" / "manifests"
    discovery_path = manifest_dir / "appeears_discovery.json"
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
    if discovery["product"]["ProductAndVersion"] != "MOD13A2.061":
        raise ValueError("Unexpected vegetation product")
    composites: dict[str, Composite] = {}
    task_records = []
    country, _ = load_boundary_geometries(Paths(root))
    cutoff = pd.Timestamp("2026-07-31T23:59:59Z")
    for manifest in sorted(manifest_dir.glob("appeears_download_*.json")):
        download = json.loads(manifest.read_text(encoding="utf-8"))
        if download.get("status") != "downloaded":
            continue
        task_id = download["task_id"]
        bundle_path = manifest_dir / f"appeears_bundle_{task_id}.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        directory = root / "datasets" / "raw" / "vegetation" / task_id
        sidecars = {}
        for item in bundle["files"]:
            path = (directory / item["file_name"]).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise ValueError("Unsafe AppEEARS bundle path")
            if path.stat().st_size != item["file_size"] or sha256_file(path) != item["sha256"]:
                raise ValueError("Downloaded vegetation source checksum mismatch")
            if path.suffix != ".tif":
                sidecars[path.name] = path
        statistics = next(p for n, p in sidecars.items() if n.endswith("-Statistics.csv"))
        stat_rows = pd.read_csv(statistics, usecols=["File Name", "Date"])
        statistics_dates = dict(zip(stat_rows["File Name"], stat_rows["Date"], strict=True))
        granules_path = next(p for n, p in sidecars.items() if n.endswith("-granule-list.txt"))
        granules = granules_path.read_text(encoding="utf-8").splitlines()
        metadata_path = next(p for n, p in sidecars.items() if n.endswith("-metadata.xml"))
        metadata = metadata_path.read_text(encoding="utf-8")
        version = re.search(r"Ver\.\s*([\d.]+)", metadata)
        request_path = next(p for n, p in sidecars.items() if n.endswith("-request.json"))
        request = json.loads(request_path.read_text(encoding="utf-8"))
        aoi = shapely.union_all(
            [shape(f["geometry"]) for f in request["params"]["geo"]["features"]]
        )
        if not shapely.covers(aoi, country):
            raise ValueError("Saved acquisition polygon does not cover the complete national area")
        task_records.append(
            {
                "task_id": task_id,
                "download_manifest": manifest.name,
                "download_manifest_sha256": sha256_file(manifest),
                "bundle_manifest_sha256": sha256_file(bundle_path),
                "sidecar_sha256": {n: sha256_file(p) for n, p in sidecars.items()},
                "service_version": version.group(1).rstrip(".") if version else None,
                "aoi_sha256": fingerprint(request.get("params", {}).get("geo")),
                "acquisition_aoi_covers_country": True,
            }
        )
        for item in bundle["files"]:
            name = Path(item["file_name"]).name
            if not name.endswith(".tif"):
                continue
            match = re.search(r"_doy(\d{7})000000_aid0001\.tif$", name)
            if match is None:
                raise ValueError("Unexpected MOD13A2 raster filename")
            key = match[1]
            start = pd.to_datetime(key, format="%Y%j")
            end = min(start + pd.Timedelta(days=16), pd.Timestamp(start.year + 1, 1, 1))
            record = composites.setdefault(key, Composite(start, end))
            role = next(
                (r for r, layer in ROLES.items() if f"__1_km_16_days_{layer}_doy" in name), None
            )
            if role is None:
                raise ValueError("Unknown vegetation raster layer")
            validate_statistics_start(name, role, str(start.date()), statistics_dates)
            source_granules = sorted(
                set(
                    re.findall(
                        rf"MOD13A2\.A{key}\.h\d{{2}}v\d{{2}}\.061\.\d{{13}}\.hdf",
                        "\n".join(granules),
                    )
                )
            )
            if not source_granules:
                raise ValueError("Raster composite lacks source granule metadata")
            if role in record.hashes and record.hashes[role] != item["sha256"]:
                raise ValueError("Overlapping bundles contain differing vegetation rasters")
            record.files[role] = directory / item["file_name"]
            record.hashes[role] = item["sha256"]
            provenance = {
                "task_id": task_id,
                "start": str(start.date()),
                "end_boundary": str(end.date()),
                "source_granules": source_granules,
                "created_utc": download.get("created_utc"),
                "retrieved_utc": download["retrieved_utc"],
            }
            if provenance not in record.provenance:
                record.provenance.append(provenance)
            productions = [
                pd.to_datetime(n.split(".")[-2], format="%Y%j%H%M%S", utc=True)
                for n in source_granules
            ]
            record.confirmation_ok |= pd.Timestamp(download["retrieved_utc"]) <= cutoff and all(
                p <= cutoff for p in productions
            )
    for record in composites.values():
        if set(record.files) != set(ROLES):
            raise ValueError("Incomplete five-layer vegetation composite")
    if not composites:
        raise ValueError("No verified complete downloaded vegetation composites")
    return sorted(composites.values(), key=lambda c: c.start), {
        "discovery_sha256": sha256_file(discovery_path),
        "tasks": task_records,
        "acquisition_buffer_degrees": discovery.get("aoi", {}).get("buffer_degrees"),
        "contract_buffer_degrees": 0.1,
        "period_derivation": "statistics and source granule start; 16-day cadence capped at Jan 1",
        "composites": {
            c.key: {
                "layer_sha256": c.hashes,
                "bundles": c.provenance,
                "confirmation_archive_proven": c.confirmation_ok,
            }
            for c in composites.values()
        },
    }
