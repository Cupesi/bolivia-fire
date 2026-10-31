from __future__ import annotations

import json

import pandas as pd
import yaml

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.grid import write_parquet
from bolivia_fire.outages import parse_outages


def pin_synthetic_outages(paths: Paths) -> str:
    snapshot = paths.datasets / "raw" / "outages" / "synthetic.html"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        """<table><tr><td>2012-020</td><td>January 20, 2012</td>
        <td>00:00:00</td><td>00:06:00</td><td>synthetic outage</td></tr></table>""",
        encoding="utf-8",
    )
    paths.manifests.mkdir(parents=True, exist_ok=True)
    table = paths.manifests / "snpp_outages.csv"
    parse_outages(snapshot.read_text()).to_csv(table, index=False)
    pin = {
        "path": snapshot.relative_to(paths.root).as_posix(),
        "sha256": sha256_file(snapshot),
        "table_path": table.relative_to(paths.root).as_posix(),
        "table_sha256": sha256_file(table),
    }
    (paths.datasets / "sources.yaml").write_text(yaml.safe_dump({"snpp_outages": pin}))
    return pin["sha256"]


def publish_synthetic_bundle(
    paths: Paths, detections: pd.DataFrame, days: pd.DataFrame, metadata: dict[bytes, bytes]
) -> None:
    interim = paths.datasets / "interim"
    bundle = interim / "detection_bundles" / "synthetic"
    bundle.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, frame in (("detections", detections), ("acquisition_days", days)):
        path = bundle / f"{name}.parquet"
        write_parquet(frame, path, metadata)
        files[path.name] = sha256_file(path)
    pointer = {
        "directory": "detection_bundles/synthetic",
        "files": files,
        "provenance": {k.decode(): v.decode() for k, v in metadata.items()},
    }
    (interim / "detection_bundle.json").write_text(json.dumps(pointer))
