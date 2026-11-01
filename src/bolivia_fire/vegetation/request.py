from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

PRODUCT = "MOD13A2.061"
LAYER_DESCRIPTIONS = {
    "ndvi": "1 km 16 days NDVI",
    "evi": "1 km 16 days EVI",
    "vi_quality": "1 km 16 days VI Quality",
    "pixel_reliability": "1 km 16 days pixel reliability",
    "composite_doy": "1 km 16 days composite day of the year",
}


def discover_layers(metadata: dict[str, Any]) -> dict[str, str]:
    selected = {}
    for role, description in LAYER_DESCRIPTIONS.items():
        matches = [
            name
            for name, item in metadata.items()
            if isinstance(item, dict)
            and item.get("Available") is True
            and item.get("Description", "").casefold() == description.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one available AppEEARS layer for {role}")
        selected[role] = matches[0]
    return selected


def bolivia_aoi(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = yaml.safe_load((root / "datasets" / "sources.yaml").read_text(encoding="utf-8"))
    pinned = sources["boundaries"]["ADM0"]
    path = (root / pinned["path"]).resolve()
    if not path.is_relative_to((root / "datasets" / "raw" / "boundaries").resolve()):
        raise ValueError("ADM0 pin is outside the boundary directory")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != pinned["sha256"]:
        raise ValueError("Pinned ADM0 checksum mismatch")
    features = json.loads(body)["features"]
    geometry = unary_union([shape(feature["geometry"]) for feature in features]).buffer(0.05)
    if not geometry.is_valid or geometry.is_empty:
        raise ValueError("Buffered ADM0 geometry is invalid")
    geo = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Bolivia", "id": 1},
                "geometry": mapping(geometry),
            }
        ],
    }
    return geo, {
        "source_path": pinned["path"],
        "source_sha256": pinned["sha256"],
        "buffer_degrees": 0.05,
        "aoi_sha256": hashlib.sha256(json.dumps(geo, sort_keys=True).encode()).hexdigest(),
    }


def year_ranges(start: date, end: date) -> list[tuple[date, date]]:
    if end < start:
        raise ValueError("End precedes start")
    return [
        (max(start, date(year, 1, 1)), min(end, date(year, 12, 31)))
        for year in range(start.year, end.year + 1)
    ]


def build_request(
    start: date,
    end: date,
    layers: list[str],
    geo: dict[str, Any],
) -> dict[str, Any]:
    if end < start:
        raise ValueError("End precedes start")
    return {
        "task_type": "area",
        "params": {
            "dates": [
                {"startDate": start.strftime("%m-%d-%Y"), "endDate": end.strftime("%m-%d-%Y")}
            ],
            "layers": [{"product": PRODUCT, "layer": layer} for layer in layers],
            "geo": geo,
            "output": {"format": {"type": "geotiff"}, "projection": "native"},
        },
    }
