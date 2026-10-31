from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from numpy.typing import NDArray

from bolivia_fire.boundaries import display_boundary
from bolivia_fire.config import Paths
from bolivia_fire.features import week_index
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.live.files import read_json, write_json, write_parquet
from bolivia_fire.panel import fit_climatology
from bolivia_fire.places import places_columnar

GRID_COLUMNS = [
    "cell_id",
    "row",
    "col",
    "department",
    "national_area_km2",
    "santa_cruz",
    "geo_holdout",
]


def export_static(
    paths: Paths, out: Path, source: Path, days: pd.DataFrame, protocol: dict[str, Any]
) -> pd.DataFrame:
    cells = (
        pd.read_parquet(paths.datasets / "processed/cells.parquet")
        .sort_values("cell_id")
        .reset_index(drop=True)
    )
    grid = {
        **{key: protocol["grid"][key] for key in ("origin", "resolution_deg", "crs")},
        "row_range": [int(cells.row.min()), int(cells.row.max())],
        "col_range": [int(cells.col.min()), int(cells.col.max())],
        **cells[GRID_COLUMNS].to_dict(orient="list"),
    }
    write_json(out / "static/grid.json", grid)
    write_parquet(out / "static/cells.parquet", cells)
    export_places_and_boundaries(paths, out, cells.cell_id.tolist())
    fitted = fit_climatology(source, cells, days, protocol)
    rows = []
    for week in range(52):
        issue = pd.Timestamp("2026-01-01") + pd.Timedelta(days=week * 7)
        rows.append(
            pd.DataFrame(
                {
                    "cell_id": cells.cell_id,
                    "week_index": week,
                    "train_climatology_cell": fitted.predict(issue),
                    "train_climatology_dept": fitted.predict_department(issue),
                }
            )
        )
    write_parquet(out / "static/train_climatology_cell.parquet", pd.concat(rows, ignore_index=True))
    return cells


def source_attributions(sources: dict[str, Any], *, retrospective: bool = False) -> list[dict]:
    entries = [
        {
            "name": "NASA FIRMS",
            "product": "VIIRS S-NPP standard processing and near-real-time",
            "license": "NASA open-data reuse with source attribution",
            "url": "https://firms.modaps.eosdis.nasa.gov/",
        }
    ]
    for level, pin in sources.get("boundaries", {}).items():
        if "license" not in pin:
            continue
        entries.append(
            {
                "name": "geoBoundaries",
                "product": f"gbOpen BOL {level}",
                "provider": pin.get("boundarySource", "GeoBolivia"),
                "license": pin["license"],
                "url": pin.get("metadata_url", "https://www.geoboundaries.org/"),
                "citation": "Runfola et al. (2020), doi:10.1371/journal.pone.0231866",
                "sha256": pin["sha256"],
                "processing": "Topology-preserving display simplification; "
                "national cell area shares.",
            }
        )
    if pin := sources.get("geonames"):
        entries.append(
            {
                "name": "GeoNames",
                "product": "Bolivia populated places, feature class P",
                "license": pin["license"],
                "license_url": pin["license_url"],
                "url": pin["url"],
                "attribution": pin["attribution"],
                "sha256": pin["sha256"],
                "processing": "Nearest place by great-circle distance; "
                "up to three places per cell.",
            }
        )
    if retrospective and (pin := sources.get("covariates", {}).get("chirps")):
        entries.append(
            {
                "name": "CHIRPS",
                "product": pin["product"],
                "provider": pin["provider"],
                "license": pin["license"],
                "license_url": pin["license_url"],
                "url": pin["product_url"],
                "citation": pin["citation_doi"],
            }
        )
    return entries


def frozen_provenance(paths: Paths, card: dict[str, Any] | None = None) -> dict[str, Any]:
    protocol_path = paths.root / "configs/protocol.yaml"
    protocol = yaml.safe_load(protocol_path.read_text(encoding="utf-8"))
    snapshots = {
        "protocol_sha256": sha256_file(protocol_path),
        "sources_sha256": sha256_file(paths.datasets / "sources.yaml"),
        "cells_sha256": sha256_file(paths.datasets / "processed/cells.parquet"),
    }
    experiments = paths.root / "configs/experiments.yaml"
    if experiments.exists():
        snapshots["experiments_sha256"] = sha256_file(experiments)
    places = paths.datasets / "processed/cell_places.parquet"
    if places.exists():
        snapshots["cell_places_sha256"] = sha256_file(places)
    result: dict[str, Any] = {
        "protocol": {
            "version": protocol["protocol_version"],
            "sha256": snapshots["protocol_sha256"],
        },
        **snapshots,
        "dataset_snapshot_ids": dict(snapshots),
    }
    log = paths.root / "artifacts/evaluation/final_test_log.json"
    openings = read_json(log) if log.exists() else []
    if not openings:
        if card is not None:
            raise ValueError("Static model provenance requires a primary opening")
        return result
    primary = openings[0]
    if primary.get("protocol_sha256") != snapshots["protocol_sha256"]:
        if card is None:
            return result
        raise ValueError("Protocol differs from primary opening")
    frozen = primary.get("dataset_snapshot_hashes", {})
    if card is not None and not frozen:
        raise ValueError("Static provenance requires snapshot hashes from the primary opening")
    if "configs/experiments.yaml" in frozen and (
        frozen["configs/experiments.yaml"] != snapshots.get("experiments_sha256")
    ):
        raise ValueError("Experiments differ from primary opening")
    result["dataset_snapshot_ids"]["panel"] = {
        Path(name).parent.name.removeprefix("year="): digest
        for name, digest in frozen.items()
        if name.startswith("datasets/processed/panel/year=") and name.endswith("/part.parquet")
    }
    if "datasets/sources.yaml" in frozen:
        result["dataset_snapshot_ids"]["evaluation_sources_sha256"] = frozen[
            "datasets/sources.yaml"
        ]
    result["primary_opening_utc"] = primary["timestamp_utc"]
    if card is not None:
        identifier = f"{card['variant']}/{card['model_name']}"
        for field, mapping_name in (
            ("source_model_sha256", "model_ids"),
            ("source_calibrator_sha256", "calibrator_ids"),
        ):
            if card[field] != primary[mapping_name][identifier]:
                raise ValueError("Static model differs from primary opening")
            result[field] = card[field]
        if card["model_id"] != card["source_model_sha256"]:
            raise ValueError("Static model id differs from primary opening")
        result.update(model_id=card["model_id"], model_name=identifier)
    return result


def export_places_and_boundaries(paths: Paths, out: Path, cell_ids: list[str]) -> dict[str, float]:
    places_path = paths.datasets / "processed/cell_places.parquet"
    if places_path.exists():
        write_json(
            out / "static/cell_places.json", places_columnar(pd.read_parquet(places_path), cell_ids)
        )
    sources = yaml.safe_load((paths.datasets / "sources.yaml").read_text(encoding="utf-8"))
    tolerances = {}
    for level in ("ADM0", "ADM1", "ADM2", "ADM3"):
        if not (pin := sources.get("boundaries", {}).get(level)):
            continue
        path = (paths.root / pin["path"]).resolve()
        if not path.is_relative_to((paths.datasets / "raw/boundaries").resolve()):
            raise ValueError("Boundary pin outside raw directory")
        data = display_boundary(path, pin["sha256"])
        write_json(out / f"static/boundaries_{level.lower()}.json", data)
        tolerances[level] = data["simplification"]["tolerance_deg"]
    return tolerances


def refresh_static_metadata(paths: Paths, out: Path, tolerances: dict[str, float]) -> None:
    meta = read_json(out / "static/meta.json")
    card = read_json(out / "model/model_card.json")
    sources = yaml.safe_load((paths.datasets / "sources.yaml").read_text(encoding="utf-8"))
    attribution = source_attributions(sources, retrospective=card["variant"] == "retrospective")
    meta.update(frozen_provenance(paths, card))
    for key in ("frozen_sp_bundle_sha256", "outage_sha256"):
        if key in meta:
            meta["dataset_snapshot_ids"][key] = meta[key]
    meta.update(
        attribution=attribution,
        source_attributions=attribution,
        boundary_simplification_tolerance_deg=tolerances,
    )
    meta["sources"] = [
        *meta.get("sources", []),
        *[
            source
            for source in attribution
            if source["name"] != "NASA FIRMS" and source not in meta.get("sources", [])
        ],
    ]
    write_json(out / "static/meta.json", meta)


def refresh_static(paths: Paths, out: Path) -> None:
    grid = read_json(out / "static/grid.json")
    tolerances = export_places_and_boundaries(paths, out, grid["cell_id"])
    refresh_static_metadata(paths, out, tolerances)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    refresh_static(Paths(), args.out)


def climatology_for_issue(
    cells: pd.DataFrame, climate: pd.DataFrame, issue: pd.Timestamp
) -> tuple[pd.DataFrame, NDArray]:
    features = (
        climate[climate.week_index.eq(week_index(issue))]
        .set_index("cell_id")
        .reindex(cells.cell_id)[["train_climatology_cell", "train_climatology_dept"]]
        .reset_index(drop=True)
    )
    baseline = features.train_climatology_cell.fillna(features.train_climatology_dept).to_numpy()
    return features, baseline


if __name__ == "__main__":
    main()
