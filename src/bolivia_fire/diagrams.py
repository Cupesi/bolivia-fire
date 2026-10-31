from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image
from shapely.geometry.base import BaseGeometry

from bolivia_fire.animations import write_gif
from bolivia_fire.config import REPO_ROOT

Renderer = Callable[[dict[str, Any]], list[Image.Image]]

PIPELINE = """flowchart LR
    subgraph archive[Observation archive]
        direction TB
        A["FIRMS SP<br/>acquisition"] --> B["Manifest checks<br/>and quarantine"]
        B --> C["Canonical<br/>detections"]
    end
    subgraph benchmark[Weekly benchmark]
        direction TB
        D["Cell-week labels<br/>outage → null"] --> E["Strict / CHIRPS<br/>features"]
        E --> F["Train, select<br/>and calibrate"]
    end
    subgraph delivery[Recorded outputs]
        direction TB
        G["Single primary<br/>test opening"] --> H[Predictions]
        H --> I["Local API<br/>and explorer"]
    end
    archive --> benchmark --> delivery
    classDef stage fill:#F3F6F8,stroke:#0072B2,color:#172C35
    class A,B,C,D,E,F,G,H,I stage
"""

PRODUCTS = """flowchart LR
    A["Retrospective benchmark<br/><br/>Final or revised products<br/>CHIRPS may postdate issue"]
    B["Availability-constrained<br/>simulation<br/><br/>Acquisition before issue
    <br/>SP ≈ NRT unvalidated"]
    C["Operational forecast<br/><br/>Not claimed<br/>Live delivery not validated"]
    A ~~~ B ~~~ C
    classDef product fill:#F3F6F8,stroke:#59717E,color:#172C35
    class A,B,C product
"""


def _browser_frames(specification: dict[str, Any]) -> list[Image.Image]:
    with tempfile.TemporaryDirectory(prefix="bolivia-fire-mermaid-") as temporary:
        specification = {**specification, "output": temporary}
        subprocess.run(
            ["node", str(REPO_ROOT / "docs" / "assets" / "diagrams" / "render.mjs")],
            input=json.dumps(specification),
            text=True,
            check=True,
            capture_output=True,
            cwd=REPO_ROOT / "frontend",
            timeout=120,
        )
        frames = []
        for path in sorted(Path(temporary).glob("frame-*.png")):
            with Image.open(path) as image:
                frames.append(image.convert("RGB"))
        return frames


def _animate(
    source: str,
    stages: list[str],
    destination: Path,
    publication_directory: Path,
    *,
    title: str,
    size: tuple[int, int],
    extra: str = "",
    renderer: Renderer | None = None,
) -> dict[str, Any]:
    directory = destination.parent / "src"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / destination.with_suffix(".mmd").name).write_text(source, encoding="utf-8")
    frames = (renderer or _browser_frames)(
        {
            "source": source,
            "stages": stages,
            "title": title,
            "width": size[0],
            "height": size[1],
            "extra": extra,
        }
    )
    if len(frames) != len(stages) + 1:
        raise ValueError("Mermaid renderer returned an unexpected frame count")
    try:
        result = write_gif(frames, destination)
    finally:
        for frame in frames:
            frame.close()
    publication_directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(destination, publication_directory / destination.name)
    return result


def animate_pipeline(
    destination: Path,
    publication_directory: Path,
    *,
    renderer: Renderer | None = None,
) -> dict[str, Any]:
    return _animate(
        PIPELINE,
        list("ABCDEFGHI"),
        destination,
        publication_directory,
        title="From satellite observations to the weekly explorer",
        size=(1120, 560),
        renderer=renderer,
    )


def partition_source(protocol: dict[str, Any], calendar: pd.DataFrame) -> str:
    source = [
        "gantt",
        "    dateFormat YYYY-MM-DD",
        "    axisFormat %Y",
        "    tickInterval 2year",
        "    todayMarker off",
        "    section Temporal roles",
    ]
    for name, identifier in [
        ("train", "Train"),
        ("val_select", "Select"),
        ("val_calib", "Calibrate"),
        ("test", "Final test"),
    ]:
        start, end = protocol["partitions"][name]
        stop = (pd.Timestamp(end) + pd.Timedelta(days=1)).date().isoformat()
        source.append(f"    {identifier} :{name}, {start}, {stop}")
    source.append("    section Purged targets")
    for index, issue in enumerate(
        pd.to_datetime(calendar.loc[calendar.partition.isna(), "issue_date"])
    ):
        source.append(f"    Boundary {issue:%Y-%m-%d} :purge{index}, {issue:%Y-%m-%d}, 7d")
    return "\n".join(source) + "\n"


def _holdout_svg(cells: pd.DataFrame, country: BaseGeometry) -> str:
    west, south, east, north = country.bounds
    blocks = []
    for block in sorted(cells.loc[cells.geo_holdout, "block_id"].unique()):
        lon, lat = [int(value) for value in block.removeprefix("b").split("_")]
        blocks.append(f'<rect class="held-out" x="{lon}" y="{-lat - 1}" width="1" height="1"/>')
    outlines = []
    boundary = country.boundary
    for line in boundary.geoms if hasattr(boundary, "geoms") else [boundary]:
        points = " ".join(f"{x},{-y}" for x, y in np.asarray(line.coords))
        outlines.append(
            f'<polyline points="{points}" fill="none" stroke="#172C35" stroke-width="0.035"/>'
        )
    return (
        f"<aside><h2>Geographic holdout</h2><p>1° blocks excluded from fitting, selection "
        f'and calibration</p><svg viewBox="{west - 0.2} {-north - 0.2} {east - west + 0.4} '
        f'{north - south + 0.4}">{"".join(blocks + outlines)}</svg>'
        "<p>Past observations may enter history features.</p></aside>"
    )


def animate_partitions(
    protocol: dict[str, Any],
    calendar: pd.DataFrame,
    cells: pd.DataFrame,
    country: BaseGeometry,
    destination: Path,
    publication_directory: Path,
    *,
    renderer: Renderer | None = None,
) -> dict[str, Any]:
    return _animate(
        partition_source(protocol, calendar),
        ["train", "val_select", "val_calib", "test", "purges"],
        destination,
        publication_directory,
        title="Temporal partitions and geographic holdout",
        size=(1320, 550),
        extra=_holdout_svg(cells, country),
        renderer=renderer,
    )


def animate_products(
    destination: Path,
    publication_directory: Path,
    *,
    renderer: Renderer | None = None,
) -> dict[str, Any]:
    return _animate(
        PRODUCTS,
        list("ABC"),
        destination,
        publication_directory,
        title="Information availability defines the product",
        size=(1320, 340),
        renderer=renderer,
    )
