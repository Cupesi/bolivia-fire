from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch, Rectangle
from matplotlib.path import Path as PlotPath
from shapely.geometry.base import BaseGeometry

from bolivia_fire.config import REPO_ROOT, Paths, load_protocol
from bolivia_fire.detections import resolve_detection_bundle
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.grid import load_boundary_geometries
from bolivia_fire.outages import load_outages

TableSource = pd.DataFrame | Path | Sequence[Path]
PARTITIONS = ("train", "val_select", "val_calib", "test")
COLORS = ("#0072B2", "#E69F00", "#009E73", "#CC79A7")
STYLE = {
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.labelsize": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "legend.fontsize": 8,
    "savefig.facecolor": "white",
    "pdf.fonttype": 42,
}


@dataclass
class DatasetFigures:
    summary: dict[str, Any]
    cells: pd.DataFrame
    training_frequency: pd.DataFrame
    weekly_detections: pd.DataFrame
    seasonal_cycle: pd.DataFrame
    issue_calendar: pd.DataFrame
    acquisition_days: pd.DataFrame
    outages: pd.DataFrame
    protocol: dict[str, Any]


def _register(connection: duckdb.DuckDBPyConnection, name: str, source: TableSource) -> None:
    if isinstance(source, pd.DataFrame):
        connection.register(name, source)
    else:
        paths = str(source) if isinstance(source, Path) else [str(path) for path in source]
        connection.read_parquet(paths, hive_partitioning=False).create_view(name)


def _observation_summary(days: pd.DataFrame, outages: pd.DataFrame) -> list[dict[str, Any]]:
    dates = pd.to_datetime(days.date, utc=True)
    missing, outage = ~days.acquired, days.outage_hours.ge(6)
    result = []
    for year in sorted(dates.dt.year.unique()):
        in_year = dates.dt.year.eq(year)
        causes: dict[str, int] = {}
        for comment, intervals in outages.groupby("comment", sort=True):
            overlaps = pd.Series(False, index=days.index)
            for interval in intervals.itertuples():
                overlaps |= (dates < interval.end_utc) & (
                    dates + pd.Timedelta(days=1) > interval.start_utc
                )
            count = int((in_year & outage & overlaps).sum())
            if count:
                causes[str(comment)] = count
        result.append(
            {
                "year": int(year),
                "days": int(in_year.sum()),
                "observed_days": int((in_year & days.observed).sum()),
                "unobserved_days": int((in_year & ~days.observed).sum()),
                "missing_acquisition_only": int((in_year & missing & ~outage).sum()),
                "documented_outage_only": int((in_year & ~missing & outage).sum()),
                "missing_acquisition_and_outage": int((in_year & missing & outage).sum()),
                "outage_causes": causes,
            }
        )
    return result


def collect_dataset(
    cells: TableSource,
    panel: TableSource,
    acquisition_days: TableSource,
    detections: TableSource,
    outages: pd.DataFrame,
    protocol: dict[str, Any],
) -> DatasetFigures:
    """Aggregate observations and label prevalence without fitting or evaluating a model."""
    with duckdb.connect(
        config={"memory_limit": "512MB", "threads": "2", "TimeZone": "UTC"}
    ) as connection:
        for name, source in (
            ("cells", cells),
            ("panel", panel),
            ("days", acquisition_days),
            ("detections", detections),
        ):
            _register(connection, name, source)
        cell_frame = connection.sql("SELECT * FROM cells ORDER BY cell_id").df()
        days = connection.sql("SELECT * FROM days ORDER BY date").df()
        partitions = connection.sql(
            """
            SELECT coalesce(partition, 'purged') AS partition, geo_holdout,
                   count(*) AS rows, count(y_primary) AS labelled_rows,
                   count(*) FILTER (WHERE y_primary = 1) AS positive_rows
            FROM panel GROUP BY partition, geo_holdout ORDER BY partition, geo_holdout
            """
        ).df()
        calendar = connection.sql(
            "SELECT DISTINCT issue_date, partition FROM panel ORDER BY issue_date"
        ).df()
        frequency = connection.sql(
            """
            SELECT cell_id,
                   avg(CAST(n_primary > 0 AS DOUBLE)) FILTER (WHERE target_complete) AS frequency
            FROM panel WHERE partition = 'train' GROUP BY cell_id ORDER BY cell_id
            """
        ).df()
        seasonal = connection.sql(
            """
            SELECT partition, least((dayofyear(issue_date)-1)//7, 51)::INTEGER AS week_index,
                   count(y_primary) AS labelled_rows,
                   avg(CAST(y_primary AS DOUBLE)) AS positive_rate
            FROM panel WHERE partition IS NOT NULL
            GROUP BY partition, week_index ORDER BY partition, week_index
            """
        ).df()
        years = connection.sql(
            """
            SELECT year(acq_datetime)::INTEGER AS year,
                   count(*) FILTER (WHERE "primary") AS selected,
                   count(*) FILTER (WHERE allconf) AS all_confidence
            FROM detections WHERE in_bolivia GROUP BY year ORDER BY year
            """
        ).df()
        weekly = connection.sql(
            """
            SELECT date_trunc('week', acq_datetime) AS issue_date,
                   count(*) FILTER (WHERE "primary") AS selected,
                   count(*) FILTER (WHERE allconf) AS all_confidence
            FROM detections WHERE in_bolivia AND year(acq_datetime) BETWEEN 2012 AND 2025
            GROUP BY issue_date ORDER BY issue_date
            """
        ).df()
        exclusions = {}
        for partition in PARTITIONS[:-1]:
            start, end = protocol["partitions"][partition]
            counts = (
                connection.execute(
                    """
                SELECT count(*) AS candidate_rows,
                  count(*) FILTER (WHERE partition IS NULL) AS purged_partition,
                  count(*) FILTER (WHERE partition IS NOT NULL AND y_primary IS NULL)
                    AS missing_label,
                  count(*) FILTER (WHERE partition IS NOT NULL AND y_primary IS NOT NULL
                    AND geo_holdout) AS geographic_holdout,
                  count(*) FILTER (WHERE partition IS NOT NULL AND y_primary IS NOT NULL
                    AND NOT geo_holdout AND NOT coalesce(window_obs_complete_28d, false))
                    AS incomplete_28d,
                  count(*) FILTER (WHERE partition IS NOT NULL AND y_primary IS NOT NULL
                    AND NOT geo_holdout AND coalesce(window_obs_complete_28d, false)) AS eligible,
                  count(*) FILTER (WHERE partition IS NOT NULL AND y_primary IS NOT NULL
                    AND NOT geo_holdout AND coalesce(window_obs_complete_28d, false)
                    AND coalesce(target_low_coverage, true)) AS low_coverage_sensitivity_additional
                FROM panel WHERE issue_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                """,
                    [start, end],
                )
                .df()
                .iloc[0]
            )
            exclusions[partition] = {name: int(value) for name, value in counts.items()}
    weekly["issue_date"] = pd.to_datetime(weekly.issue_date, utc=True).dt.tz_localize(None)
    first = pd.Timestamp(days.date.min()).to_period("W-SUN").start_time
    last = min(pd.Timestamp(days.date.max()), pd.Timestamp("2025-12-31"))
    weeks = pd.date_range(max(first, pd.Timestamp("2011-12-26")), last, freq="W-MON")
    weekly = weekly.set_index("issue_date").reindex(weeks, fill_value=0)
    weekly.index.name = "issue_date"
    weekly = weekly.reset_index()
    statistics: dict[str, dict[str, Any]] = {}
    for row in partitions.itertuples(index=False):
        population = "held_out" if row.geo_holdout else "non_held_out"
        statistics.setdefault(row.partition, {})[population] = {
            "rows": int(row.rows),
            "labelled_rows": int(row.labelled_rows),
            "positive_rows": int(row.positive_rows),
            "labelled_share": row.labelled_rows / row.rows if row.rows else None,
            "positive_rate": row.positive_rows / row.labelled_rows if row.labelled_rows else None,
        }
    summary = {
        "protocol_version": protocol["protocol_version"],
        "description": "Descriptive observations and label prevalence; no performance metrics.",
        "eligible_cells": len(cell_frame),
        "national_area_km2": float(cell_frame.national_area_km2.sum()),
        "held_out_cells": int(cell_frame.geo_holdout.sum()),
        "held_out_area_km2": float(
            cell_frame.loc[cell_frame.geo_holdout, "national_area_km2"].sum()
        ),
        "eligible_blocks": int(cell_frame.block_id.nunique()),
        "held_out_blocks": int(cell_frame.loc[cell_frame.geo_holdout, "block_id"].nunique()),
        "issue_weeks": len(calendar),
        "first_issue": pd.Timestamp(calendar.issue_date.min()).date().isoformat(),
        "last_issue": pd.Timestamp(calendar.issue_date.max()).date().isoformat(),
        "rows_per_partition": {
            str(name): int(count)
            for name, count in partitions.groupby("partition").rows.sum().items()
        },
        "partition_statistics": statistics,
        "observation_days_per_year": _observation_summary(days, outages),
        "detections_per_year": years.to_dict(orient="records"),
        "fitting_exclusion_order": [
            "purged_partition",
            "missing_label",
            "geographic_holdout",
            "incomplete_28d",
        ],
        "fitting_exclusions": exclusions,
        "definitions": {
            "positive_rate": "Positive / nonmissing primary labels, before fitting filters.",
            "seasonal_cycle": "Equal weight per labelled cell-week; both geographic populations.",
            "training_map": "Complete, non-purged train weeks with n_primary > 0, per cell.",
            "outage_causes": "Outage days overlapping source comments; causes may overlap.",
            "fitting_exclusions": "Sequential, disjoint counts within issue-date partition bounds.",
            "low_coverage_sensitivity": "Additional exclusions from eligible rows; not primary.",
            "weekly_counts": "National observations, including incomplete weeks; 2012-2025 only.",
        },
    }
    return DatasetFigures(
        summary, cell_frame, frequency, weekly, seasonal, calendar, days, outages.copy(), protocol
    )


def write_dataset_summary(data: DatasetFigures, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(data.summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return data.summary


def _outline(ax: Axes, geometry: BaseGeometry, **kwargs: Any) -> None:
    boundary = geometry.boundary
    lines = shapely.get_parts(boundary)
    for line in lines:
        coordinates = np.asarray(line.coords)
        ax.plot(coordinates[:, 0], coordinates[:, 1], **kwargs)


def _country_path(country: BaseGeometry) -> PlotPath:
    rings = []
    for polygon in shapely.get_parts(country):
        for ring in (polygon.exterior, *polygon.interiors):
            points = np.asarray(ring.coords)
            codes = np.full(len(points), PlotPath.LINETO, dtype=np.uint8)
            codes[0], codes[-1] = PlotPath.MOVETO, PlotPath.CLOSEPOLY
            rings.append(PlotPath(points, codes))
    return PlotPath.make_compound_path(*rings)


def _holdout(ax: Axes, cells: pd.DataFrame, *, fill: bool = False) -> None:
    for block in sorted(cells.loc[cells.geo_holdout, "block_id"].unique()):
        west, south = (int(value) for value in block.removeprefix("b").split("_"))
        ax.add_patch(
            Rectangle(
                (west, south),
                1,
                1,
                facecolor="#E69F00" if fill else "none",
                edgecolor="#D58A00",
                linewidth=0.65,
                alpha=0.55 if fill else 0.9,
            )
        )


def _map_axes(ax: Axes, country: BaseGeometry) -> None:
    west, south, east, north = country.bounds
    ax.set(xlim=(west - 0.3, east + 0.3), ylim=(south - 0.3, north + 0.3))
    ax.set_aspect(1 / np.cos(np.deg2rad((south + north) / 2)))
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")


def plot_training_map(
    data: DatasetFigures, country: BaseGeometry, departments: Mapping[str, BaseGeometry]
) -> Figure:
    figure, ax = plt.subplots(figsize=(4.9, 4.9), layout="constrained")
    cells = data.cells.merge(data.training_frequency, on="cell_id", how="left")
    west, south = cells.west.min(), cells.south.min()
    resolution = float((cells.east - cells.west).median())
    cols = np.rint((cells.west - west) / resolution).astype(int)
    rows = np.rint((cells.south - south) / resolution).astype(int)
    values = np.full((rows.max() + 1, cols.max() + 1), np.nan)
    values[rows, cols] = cells.frequency * 100
    mesh = ax.pcolormesh(
        west + np.arange(values.shape[1] + 1) * resolution,
        south + np.arange(values.shape[0] + 1) * resolution,
        values,
        cmap=plt.get_cmap("cividis", 64),
        vmin=0,
        vmax=max(float(cells.frequency.max() * 100), 1),
        rasterized=True,
    )
    mesh.set_clip_path(PathPatch(_country_path(country), transform=ax.transData))
    for geometry in departments.values():
        _outline(ax, geometry, color="#FFFFFF", linewidth=0.45)
    _outline(ax, country, color="#333333", linewidth=0.75)
    _holdout(ax, cells)
    _map_axes(ax, country)
    ax.set_title("Training-period detection frequency")
    figure.colorbar(mesh, ax=ax, shrink=0.78, label="Observed train weeks with ≥1 detection (%)")
    ax.legend(
        handles=[
            Patch(facecolor="none", edgecolor="#D58A00", label="Geographic holdout (1° blocks)")
        ],
        loc="lower left",
        framealpha=0.95,
    )
    return figure


def _shade_missing(ax: Axes, days: pd.DataFrame) -> None:
    missing = pd.DatetimeIndex(days.loc[~days.acquired, "date"])
    for group in np.split(missing, np.flatnonzero(np.diff(missing.asi8) > 86_400_000_000_000) + 1):
        if len(group):
            ax.axvspan(group[0], group[-1] + pd.Timedelta(days=1), color="#777777", alpha=0.18)


def plot_weekly_detections(data: DatasetFigures) -> Figure:
    figure, ax = plt.subplots(figsize=(9.0, 3.4), layout="constrained")
    weeks = data.weekly_detections
    ax.plot(weeks.issue_date, weeks.all_confidence, color="#8DA0AB", linewidth=0.6)
    ax.plot(weeks.issue_date, weeks.selected, color=COLORS[0], linewidth=0.8)
    _shade_missing(ax, data.acquisition_days)
    for outage in data.outages.itertuples():
        ax.axvspan(
            pd.Timestamp(outage.start_utc).tz_localize(None),
            pd.Timestamp(outage.end_utc).tz_localize(None),
            color=COLORS[1],
            alpha=0.25,
        )
    ax.set(
        xlim=(weeks.issue_date.min(), weeks.issue_date.max() + pd.Timedelta(days=7)),
        ylim=(0, None),
        ylabel="Detections / week",
        xlabel="Acquisition week (UTC)",
        title="National VIIRS S-NPP detections, 2012–2025",
    )
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.ticklabel_format(axis="y", style="plain")
    ax.legend(
        handles=[
            Line2D([], [], color=COLORS[0], label="Nominal + high"),
            Line2D([], [], color="#8DA0AB", label="All confidence"),
            Patch(facecolor=COLORS[1], alpha=0.25, label="Documented outage"),
            Patch(facecolor="#777777", alpha=0.18, label="Not acquired"),
        ],
        loc="upper left",
        ncols=2,
    )
    return figure


def plot_seasonal_cycle(data: DatasetFigures) -> Figure:
    figure, ax = plt.subplots(figsize=(7.2, 3.7), layout="constrained")
    for partition, color in zip(PARTITIONS, COLORS, strict=True):
        rows = data.seasonal_cycle[data.seasonal_cycle.partition == partition]
        series = rows.set_index("week_index").positive_rate.reindex(range(52))
        start, end = data.protocol["partitions"][partition]
        years = start[:4] if start[:4] == end[:4] else f"{start[:4]}–{end[:4]}"
        label = f"{partition} ({years})"
        ax.plot(series.index, series * 100, color=color, label=label, linewidth=1.4)
    ax.set(
        xlim=(0, 51),
        ylim=(0, None),
        xlabel="Calendar week index (0–51)",
        ylabel="Positive cell-weeks (% of labelled rows)",
        title="Seasonal cycle of detections",
    )
    ax.set_xticks([0, 8, 17, 26, 35, 43, 51])
    ax.legend(loc="upper left", frameon=False)
    return figure


def plot_partition_timeline(data: DatasetFigures, country: BaseGeometry) -> Figure:
    figure, (ax, inset) = plt.subplots(
        1, 2, figsize=(9, 4), gridspec_kw={"width_ratios": [3.2, 1.2]}, layout="constrained"
    )
    for index, (partition, color) in enumerate(zip(PARTITIONS, COLORS, strict=True)):
        subset = data.issue_calendar[data.issue_calendar.partition == partition]
        for issue in pd.to_datetime(subset.issue_date):
            left = mdates.date2num(issue)
            ax.broken_barh([(left, 7)], (index - 0.28, 0.56), facecolors=color, linewidth=0)
        start, end = data.protocol["partitions"][partition]
        years = start[:4] if start[:4] == end[:4] else f"{start[:4]}–{end[:4]}"
        ax.text(
            pd.Timestamp(start) + (pd.Timestamp(end) - pd.Timestamp(start)) / 2,
            index,
            years,
            ha="center",
            va="center",
            fontsize=8,
            bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "none", "pad": 1.5},
        )
    purged = data.issue_calendar[data.issue_calendar.partition.isna()]
    for issue in pd.to_datetime(purged.issue_date):
        ax.axvspan(issue, issue + pd.Timedelta(days=7), facecolor="#555555", alpha=0.35)
        ax.annotate(
            issue.strftime("%Y-%m-%d"),
            (issue, -0.45),
            xytext=(0, -15),
            textcoords="offset points",
            rotation=55,
            ha="right",
            va="top",
            fontsize=7,
        )
    first = pd.Timestamp(data.issue_calendar.issue_date.min())
    last = pd.Timestamp(data.issue_calendar.issue_date.max()) + pd.Timedelta(days=7)
    ax.set(
        yticks=range(4),
        yticklabels=PARTITIONS,
        ylim=(4.0, -1.6),
        xlim=(first, last),
        xlabel="Issue date (UTC)",
        title="Temporal partitions and purged target weeks",
    )
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.legend(
        handles=[Patch(facecolor="#555555", alpha=0.35, label="Purged 7-day target")],
        loc="lower left",
        frameon=False,
    )
    _holdout(inset, data.cells, fill=True)
    _outline(inset, country, color="#333333", linewidth=0.7)
    _map_axes(inset, country)
    inset.set_title("Geographic holdout", fontsize=10)
    inset.tick_params(labelsize=7)
    inset.text(
        0.02,
        -0.17,
        "Excluded from fitting,\nselection and calibration",
        transform=inset.transAxes,
        fontsize=8,
        va="top",
    )
    return figure


def write_dataset_figures(
    data: DatasetFigures,
    country: BaseGeometry,
    departments: Mapping[str, BaseGeometry],
    *,
    artifact_directory: Path,
    pdf_directory: Path,
) -> dict[str, Path]:
    artifact_directory.mkdir(parents=True, exist_ok=True)
    pdf_directory.mkdir(parents=True, exist_ok=True)
    outputs = {}
    with plt.rc_context(STYLE):
        for name, make in (
            ("map", lambda: plot_training_map(data, country, departments)),
            ("weekly", lambda: plot_weekly_detections(data)),
            ("seasonal", lambda: plot_seasonal_cycle(data)),
            ("partitions", lambda: plot_partition_timeline(data, country)),
        ):
            figure = make()
            try:
                output = artifact_directory / f"dataset_{name}.png"
                figure.savefig(output, dpi=300, pil_kwargs={"optimize": True})
                figure.savefig(pdf_directory / f"dataset_{name}.pdf", dpi=300)
                outputs[name] = output
            finally:
                plt.close(figure)
    return outputs


def build_dataset_artifacts(root: Path = REPO_ROOT) -> dict[str, Any]:
    paths, protocol = Paths(root), load_protocol(root)
    detections, days, provenance = resolve_detection_bundle(paths, protocol)
    panels = sorted((paths.datasets / "processed" / "panel").glob("year=*/part.parquet"))
    if not panels:
        raise ValueError("No local panel partitions are available")
    outages, _ = load_outages(paths)
    cells = paths.datasets / "processed" / "cells.parquet"
    data = collect_dataset(cells, panels, days, detections, outages, protocol)
    data.summary["input_sha256"] = {
        **{name.decode(): value.decode() for name, value in provenance.items()},
        "cells": sha256_file(cells),
        "panel": {source.relative_to(root).as_posix(): sha256_file(source) for source in panels},
    }
    write_dataset_figures(
        data,
        *load_boundary_geometries(paths),
        artifact_directory=root / "artifacts" / "figures",
        pdf_directory=root / "data" / "figures",
    )
    return write_dataset_summary(data, root / "artifacts" / "figures" / "dataset_summary.json")


def partition_summary_table(summary: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for partition in PARTITIONS:
        for population, values in summary["partition_statistics"].get(partition, {}).items():
            rows.append({"partition": partition, "population": population, **values})
    return pd.DataFrame(rows).round({"labelled_share": 4, "positive_rate": 4})


def show_dataset_figure(name: str, root: Path = REPO_ROOT) -> None:
    from IPython.display import Image, display

    if name not in {"map", "weekly", "seasonal", "partitions"}:
        raise ValueError("Unknown dataset figure")
    display(Image(filename=str(root / "artifacts" / "figures" / f"dataset_{name}.png"), width=800))
