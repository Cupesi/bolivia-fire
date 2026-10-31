from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from matplotlib import font_manager
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
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "TeX Gyre Termes", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.labelsize": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "legend.fontsize": 8,
    "savefig.facecolor": "white",
    "pdf.fonttype": 42,
    "axes.titlepad": 12,
    "figure.constrained_layout.w_pad": 0.2,
    "figure.constrained_layout.h_pad": 0.2,
}


@contextmanager
def chart_style() -> Iterator[str]:
    with plt.rc_context(STYLE):
        try:
            path = font_manager.findfont(
                font_manager.FontProperties(family=STYLE["font.serif"]), fallback_to_default=False
            )
        except ValueError as error:
            raise RuntimeError("No approved serif chart font is available") from error
        family = font_manager.get_font(path).family_name
        if family.casefold() not in {name.casefold() for name in STYLE["font.serif"]}:
            raise RuntimeError(f"Chart font must resolve to an approved serif; resolved {family}")
        yield family


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
        loc="upper center",
        bbox_to_anchor=(0.5, -0.22),
        framealpha=0.95,
    )
    return figure


def _shade_missing(ax: Axes, days: pd.DataFrame) -> None:
    missing = pd.DatetimeIndex(days.loc[~days.acquired, "date"])
    gaps = np.flatnonzero((missing[1:] - missing[:-1]) > pd.Timedelta(days=1)) + 1
    for group in np.split(missing, gaps):
        if len(group):
            ax.axvspan(group[0], group[-1] + pd.Timedelta(days=1), color="#777777", alpha=0.18)


def unobserved_spans(days: pd.DataFrame) -> pd.DataFrame:
    dates = pd.DatetimeIndex(pd.to_datetime(days.loc[days.outage_hours.ge(6), "date"], utc=True))
    dates = dates.tz_localize(None).sort_values().unique()
    groups = np.split(dates, np.flatnonzero((dates[1:] - dates[:-1]) > pd.Timedelta(days=1)) + 1)
    return pd.DataFrame(
        [
            {"start_utc": group[0], "end_utc": group[-1] + pd.Timedelta(days=1)}
            for group in groups
            if len(group)
        ],
        columns=["start_utc", "end_utc"],
    )


def plot_weekly_detections(data: DatasetFigures) -> Figure:
    figure, ax = plt.subplots(figsize=(9.0, 3.4), layout="constrained")
    weeks = data.weekly_detections
    ax.plot(weeks.issue_date, weeks.all_confidence, color="#9AB4C0", linewidth=1.8)
    ax.plot(weeks.issue_date, weeks.selected, color=COLORS[0], linewidth=0.8)
    _shade_missing(ax, data.acquisition_days)
    for outage in unobserved_spans(data.acquisition_days).itertuples():
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
            Line2D([], [], color="#9AB4C0", label="All confidence"),
            Patch(facecolor=COLORS[1], alpha=0.25, label="Unobserved (sensor outage >= 6 h)"),
            Patch(facecolor="#777777", alpha=0.18, label="Not acquired"),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.25),
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
        -0.28,
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
) -> dict[str, Path]:
    artifact_directory.mkdir(parents=True, exist_ok=True)
    outputs = {}
    with chart_style() as font:
        for name, make in (
            ("map", lambda: plot_training_map(data, country, departments)),
            ("weekly", lambda: plot_weekly_detections(data)),
            ("seasonal", lambda: plot_seasonal_cycle(data)),
            ("partitions", lambda: plot_partition_timeline(data, country)),
        ):
            figure = make()
            try:
                output = artifact_directory / f"dataset_{name}.png"
                figure.savefig(
                    output,
                    dpi=300,
                    bbox_inches="tight",
                    pad_inches=0.25,
                    pil_kwargs={"optimize": True},
                    metadata={"FontFamily": font, "MathTextFontSet": STYLE["mathtext.fontset"]},
                )
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


RESULT_NAMES = ("temporal", "reliability", "per_year", "geographic", "area_budget", "map", "h2")
RESULT_LABELS = {
    "train_climatology": "Climatology",
    "persistence": "Persistence",
    "logistic_regression": "Logistic regression",
}


@dataclass
class ResultsFigures:
    summary: dict[str, Any]


@dataclass
class ResultsMap:
    cells: pd.DataFrame
    summary: dict[str, Any]


def _result_label(model: str, version: str) -> str:
    label = RESULT_LABELS.get(model, "LightGBM")
    return f"{label} (calibrated)" if version == "calibrated" else label


def _result_models(population: dict[str, Any]) -> list[tuple[str, str]]:
    lightgbm = [name for name in population["models"] if name.startswith("lightgbm_")]
    if len(lightgbm) != 1:
        raise ValueError("Expected exactly one selected LightGBM in recorded evaluation")
    return [
        ("train_climatology", "uncalibrated"),
        ("persistence", "uncalibrated"),
        ("logistic_regression", "calibrated"),
        (lightgbm[0], "calibrated"),
    ]


def _result_population(report: dict[str, Any], name: str, source: str) -> dict[str, Any]:
    population = report if name == "temporal" else report["geographic_holdout"]
    prefix = "models" if name == "temporal" else "geographic_holdout.models"
    baseline = population["models"][population["best_baseline"]]["uncalibrated"]
    rows = []
    for model, version in _result_models(population):
        metrics = population["models"][model][version]
        bootstrap = report["candidate_bootstrap"][name][model][version]
        ap, baseline_ap = metrics["average_precision"], baseline["average_precision"]
        common_support = metrics["rows"] == baseline["rows"] == bootstrap["paired_rows"]
        difference = (
            ap - baseline_ap
            if common_support and ap is not None and baseline_ap is not None
            else None
        )
        rows.append(
            {
                "model": model,
                "label": _result_label(model, version),
                "probability_version": version,
                "average_precision": ap,
                "ap_interval": bootstrap["metrics"]["average_precision"],
                "paired_difference": difference,
                "paired_difference_derived": difference is not None,
                "paired_difference_method": "Subtract recorded APs only on identical support.",
                "paired_interval": bootstrap["paired_difference"]["average_precision"],
                "paired_rows": bootstrap["paired_rows"],
                "rows": metrics["rows"],
                "replicates": bootstrap["replicates"],
                "blocks": bootstrap["blocks"],
                "confidence_level": bootstrap["confidence_level"],
                "metric_key": f"{prefix}.{model}.{version}.average_precision",
                "interval_key": f"candidate_bootstrap.{name}.{model}.{version}",
            }
        )
    return {
        "source": source,
        "selected_model": population["selected_model"],
        "best_baseline": population["best_baseline"],
        "models": rows,
        "caption": (
            (
                "Temporal test, strict; "
                if name == "temporal"
                else "Geographic holdout, strict_geo; "
            )
            + "2023–2025. Original baseline probabilities; fitted candidates are calibrated. "
            "Points are recorded APs; differences subtract recorded APs on identical support. "
            f"95% paired {'calendar-month' if name == 'temporal' else 'one-degree spatial'}-block "
            "bootstrap intervals use 500 recorded replicates. "
            f"Comparator: {RESULT_LABELS[population['best_baseline']]} (chosen on val_select)."
        ),
    }


def collect_results(root: Path = REPO_ROOT) -> ResultsFigures:
    directory = root / "artifacts" / "evaluation"
    paths = [directory / "test_strict.json", directory / "final_test_log.json"]
    paths += sorted(directory.glob("selection_*.json"))
    raw = {path: path.read_bytes() for path in paths}
    documents = {path.name: json.loads(content) for path, content in raw.items()}
    report = documents["test_strict.json"]
    if report["split"] != "test" or report["variant"] != "strict":
        raise ValueError("Results require the recorded strict final-test evaluation")
    for name, population in (("strict", report), ("strict_geo", report["geographic_holdout"])):
        selection = documents[f"selection_{name}.json"]
        for key in ("selected_model", "best_baseline"):
            if selection[key] != population[key]:
                raise ValueError(f"Recorded selection disagrees with evaluation: {name}.{key}")
        for key in ("protocol_sha256", "experiments_sha256"):
            if selection[key] != report[key]:
                raise ValueError(f"Recorded selection provenance disagrees: {name}.{key}")
        for model, _ in _result_models(population):
            identifier = f"{name}/{model}"
            for key in ("model_ids", "calibrator_ids"):
                if key in report and selection[key][model] != report[key][identifier]:
                    raise ValueError(f"Recorded selection model hash disagrees: {identifier}")
    log = documents["final_test_log.json"]
    if report["final_test_opening"] not in log:
        raise ValueError("Recorded final-test opening is absent from the access log")
    source = "artifacts/evaluation/test_strict.json"
    temporal = _result_population(report, "temporal", source)
    geographic = _result_population(report, "geographic", source)
    reliability = []
    for model, version in (
        (report["selected_model"], "calibrated"),
        (report["best_baseline"], "uncalibrated"),
    ):
        reliability.append(
            {
                "model": model,
                "label": _result_label(model, version),
                "probability_version": version,
                "bins": report["models"][model][version]["reliability"],
                "source_key": f"models.{model}.{version}.reliability",
            }
        )
    annual = []
    for year in (2023, 2024, 2025):
        for model, version in _result_models(report):
            annual.append(
                {
                    "year": year,
                    "model": model,
                    "label": _result_label(model, version),
                    "probability_version": version,
                    "average_precision": report["per_year"][str(year)][model][version][
                        "average_precision"
                    ],
                    "source_key": f"per_year.{year}.{model}.{version}.average_precision",
                }
            )
    budgets = []
    for name, population in (("temporal", report), ("geographic", report["geographic_holdout"])):
        prefix = "models" if name == "temporal" else "geographic_holdout.models"
        values = []
        for model, version in _result_models(population):
            for budget in (5, 10):
                key = f"recall_at_area_budget_{budget}pct"
                values.append(
                    {
                        "model": model,
                        "label": _result_label(model, version),
                        "area_budget_pct": budget,
                        "recall": population["models"][model][version][key],
                        "source_key": f"{prefix}.{model}.{version}.{key}",
                    }
                )
        budgets.append({"population": name, "values": values})
    h2_bootstrap = report.get("strict_retrospective_bootstrap", {})
    h2_interval = h2_bootstrap.get("paired_difference", {}).get("average_precision")
    figures = {
        "temporal": temporal,
        "geographic": geographic,
        "reliability": {
            "source": source,
            "models": reliability,
            "caption": "Temporal test, 2023–2025. Recorded equal-frequency reliability bins; "
            "each point retains its recorded bin mass. The second panel expands "
            "the low-probability range; the dashed line is perfect reliability.",
        },
        "per_year": {
            "source": source,
            "values": annual,
            "caption": "Temporal-test AP by issue year. Original baselines and calibrated "
            "fitted candidates; all values are recorded, with no annual intervals.",
        },
        "area_budget": {
            "source": source,
            "populations": budgets,
            "caption": "Recorded recall at 5% and 10% of eligible cell area per issue week. "
            "Crossing cells contribute fractionally; positive contributions are "
            "pooled over weeks. Original baselines; calibrated fitted candidates.",
        },
        "h2": {
            "source": source,
            "source_key": "strict_retrospective_bootstrap.paired_difference.average_precision",
            "estimate": None,
            "interval": h2_interval,
            "replicates": h2_bootstrap.get("replicates"),
            "paired_rows": h2_bootstrap.get("paired_rows"),
            "decision": report["hypotheses"]["H2"],
            "caption": "H2: retrospective minus strict AP, calibrated selected candidates. "
            "The recorded 95% paired month-block interval is shown without a point: "
            "the artifact does not record the paired point estimate. The frozen "
            "H2 rule tests whether this interval contains zero; it is not an "
            "equivalence test.",
        },
    }
    return ResultsFigures(
        {
            "description": "Results copied from closed artifacts; no metrics recomputed.",
            "sources": {
                path.relative_to(root).as_posix(): {"sha256": hashlib.sha256(content).hexdigest()}
                for path, content in raw.items()
            },
            "selection": {
                name.removesuffix(".json"): {
                    "selected_model": value["selected_model"],
                    "best_baseline": value["best_baseline"],
                    "model_ids": value.get("model_ids", {}),
                }
                for name, value in documents.items()
                if name.startswith("selection_")
            },
            "final_test_access": {
                "source": "artifacts/evaluation/final_test_log.json",
                "recorded_openings_at_read": len(log),
                "evaluation_opening": report["final_test_opening"]["timestamp_utc"],
            },
            "figures": figures,
        }
    )


def collect_results_map(
    cells: TableSource,
    panel: TableSource,
    predictions: TableSource,
    selected_model: str,
    geographic_model: str,
) -> ResultsMap:
    with duckdb.connect(config={"memory_limit": "512MB", "threads": "2"}) as connection:
        for name, table in (
            ("map_cells", cells),
            ("map_labels", panel),
            ("map_predictions", predictions),
        ):
            _register(connection, name, table)
        connection.execute("SET VARIABLE selected_model = ?", [f"strict/{selected_model}"])
        connection.execute("SET VARIABLE geographic_model = ?", [f"strict_geo/{geographic_model}"])
        connection.execute(
            """
            CREATE TEMP VIEW map_rows AS
            SELECT c.*, l.issue_date, l.n_primary, l.y_primary, l.target_complete,
                   p.p, p.y AS exported_y
            FROM map_labels l JOIN map_cells c USING (cell_id)
            JOIN map_predictions p ON p.cell_id = l.cell_id AND p.issue_date = l.issue_date
              AND p.model = CASE WHEN c.geo_holdout THEN getvariable('geographic_model')
                                 ELSE getvariable('selected_model') END
            WHERE year(l.issue_date) = 2024 AND l.partition = 'test'
            """
        )
        weeks = connection.sql(
            """
            SELECT issue_date, sum(n_primary) AS detections
            FROM map_rows GROUP BY issue_date
            HAVING bool_and(target_complete) AND count(y_primary) = count(*)
              AND count(DISTINCT cell_id) = (SELECT count(*) FROM map_cells)
              AND count(*) = count(DISTINCT cell_id) AND bool_and(isfinite(p))
            ORDER BY detections DESC, issue_date ASC LIMIT 1
            """
        ).fetchall()
        if not weeks:
            raise ValueError("No complete 2024 week with national prediction coverage")
        week = weeks[0]
        frame = connection.execute(
            "SELECT * FROM map_rows WHERE issue_date = ? ORDER BY cell_id", [week[0]]
        ).df()
    if not frame.y_primary.eq(frame.exported_y).all() or not frame.p.between(0, 1).all():
        raise ValueError("Map predictions and panel labels disagree or probabilities are invalid")
    issue = pd.Timestamp(week[0])
    summary = {
        "derived": True,
        "selection_rule": "Largest n_primary sum among complete 2024 target weeks with national "
        "prediction coverage; ties choose the earliest issue date.",
        "issue_date": issue.date().isoformat(),
        "target_end_exclusive": (issue + pd.Timedelta(days=7)).date().isoformat(),
        "detections": int(week[1]),
        "positive_cells": int(frame.y_primary.sum()),
        "cells": len(frame),
        "temporal_model": f"strict/{selected_model}",
        "geographic_model": f"strict_geo/{geographic_model}",
        "values": {
            column: frame[column].tolist()
            for column in ("cell_id", "west", "south", "p", "y_primary", "n_primary", "geo_holdout")
        },
        "caption": f"Issue {issue.date().isoformat()}, target ["
        f"{issue.date().isoformat()}, {(issue + pd.Timedelta(days=7)).date().isoformat()}) "
        "UTC. Derived example: largest observed detection total among complete 2024 "
        "weeks with national prediction coverage; earliest tie. Calibrated strict "
        "predictions outside held-out blocks, strict_geo predictions inside them. "
        "Right: observed primary detection count per cell (log1p color scale). "
        "ADM1 outlines are pinned boundaries. Week selection and counts are descriptive.",
    }
    return ResultsMap(frame, summary)


def _result_caption(figure: Figure, text: str) -> None:
    import textwrap

    figure.supxlabel(textwrap.fill(text, width=110), fontsize=9, ha="center")


def _result_interval(ax: Axes, y: int, interval: dict[str, Any] | None, color: str) -> None:
    if interval is not None and interval.get("lower") is not None:
        lower, upper = interval["lower"], interval["upper"]
        ax.plot([lower, upper], [y, y], color=color, linewidth=1.5)
        ax.plot([lower, upper], [y, y], "|", color=color, markersize=7)


def plot_results_ap(data: ResultsFigures, population: str) -> Figure:
    values = data.summary["figures"][population]
    figure, axes = plt.subplots(1, 2, figsize=(10, 4.4), sharey=True, layout="constrained")
    for y, (row, color) in enumerate(zip(values["models"], COLORS, strict=True)):
        for ax, metric, interval in (
            (axes[0], "average_precision", "ap_interval"),
            (axes[1], "paired_difference", "paired_interval"),
        ):
            _result_interval(ax, y, row[interval], color)
            if row[metric] is not None:
                ax.plot(row[metric], y, "o", color=color, markersize=5)
            ax.grid(axis="x", alpha=0.15)
    axes[0].set(
        yticks=range(4),
        yticklabels=[row["label"] for row in values["models"]],
        ylim=(3.6, -0.6),
        xlabel="Average precision (AP)",
        title="Recorded AP and 95% CI",
    )
    axes[0].set_xlim(left=0)
    axes[1].axvline(0, color="#777777", linewidth=0.8, linestyle="--")
    axes[1].set(
        xlabel="AP difference (model − best baseline)", title="Paired difference and 95% CI"
    )
    axes[1].tick_params(axis="y", left=False)
    figure.suptitle(
        "Temporal test · strict" if population == "temporal" else "Geographic holdout · strict_geo"
    )
    _result_caption(figure, values["caption"])
    return figure


def plot_results_reliability(data: ResultsFigures) -> Figure:
    values = data.summary["figures"]["reliability"]
    figure, axes = plt.subplots(1, 2, figsize=(9.4, 4.6), layout="constrained")
    maximum = max(
        bin_[key]
        for row in values["models"]
        for bin_ in row["bins"]
        for key in ("mean_probability", "positive_rate")
    )
    for ax, limit, title in zip(
        axes,
        (max(0.02, maximum * 1.12), 0.01),
        ("All recorded bins", "Low-probability detail"),
        strict=True,
    ):
        ax.plot([0, limit], [0, limit], "--", color="#777777", linewidth=0.9)
        for row, color, marker in zip(
            values["models"], (COLORS[3], COLORS[0]), ("o", "s"), strict=True
        ):
            bins = [
                bin_
                for bin_ in row["bins"]
                if bin_["mean_probability"] <= limit and bin_["positive_rate"] <= limit
            ]
            ax.plot(
                [bin_["mean_probability"] for bin_ in bins],
                [bin_["positive_rate"] for bin_ in bins],
                marker=marker,
                color=color,
                label=row["label"],
                linewidth=1.2,
                markersize=4,
            )
        ax.set(
            xlim=(-limit * 0.04, limit * 1.04),
            ylim=(-limit * 0.04, limit * 1.04),
            xlabel="Mean predicted probability",
            ylabel="Observed positive fraction",
            title=title,
        )
        ax.set_aspect("equal")
        ax.grid(alpha=0.15)
    axes[0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.28), frameon=False)
    axes[1].text(
        0.03,
        0.96,
        "Remaining bins outside this view",
        transform=axes[1].transAxes,
        va="top",
        fontsize=8,
    )
    _result_caption(figure, values["caption"])
    return figure


def plot_results_per_year(data: ResultsFigures) -> Figure:
    values = data.summary["figures"]["per_year"]
    figure, ax = plt.subplots(figsize=(7.4, 4.5), layout="constrained")
    for model, color, marker in zip(
        dict.fromkeys(row["model"] for row in values["values"]),
        COLORS,
        ("o", "s", "^", "D"),
        strict=True,
    ):
        rows = [row for row in values["values"] if row["model"] == model]
        ax.plot(
            [row["year"] for row in rows],
            [row["average_precision"] for row in rows],
            color=color,
            marker=marker,
            label=rows[0]["label"],
            linewidth=1.3,
        )
    ax.set(
        xticks=[2023, 2024, 2025],
        xlabel="Issue year (UTC)",
        ylabel="Average precision (AP)",
        ylim=(0, None),
        title="Temporal test by year",
    )
    ax.grid(axis="y", alpha=0.15)
    ax.legend(loc="best", frameon=False)
    _result_caption(figure, values["caption"])
    return figure


def plot_results_area_budget(data: ResultsFigures) -> Figure:
    values = data.summary["figures"]["area_budget"]
    figure, axes = plt.subplots(1, 2, figsize=(10, 4.8), sharey=True, layout="constrained")
    for ax, population in zip(axes, values["populations"], strict=True):
        rows = population["values"]
        for index, model in enumerate(dict.fromkeys(row["model"] for row in rows)):
            group = [row for row in rows if row["model"] == model]
            ax.plot(
                [row["area_budget_pct"] for row in group],
                [None if row["recall"] is None else row["recall"] * 100 for row in group],
                marker=("o", "s", "^", "D")[index],
                color=COLORS[index],
                label=group[0]["label"],
                linewidth=1.3,
            )
        ax.set(
            xticks=[5, 10],
            xlim=(4.5, 10.5),
            ylim=(0, 100),
            xlabel="Weekly area budget (%)",
            title="Temporal · strict"
            if population["population"] == "temporal"
            else "Geographic · strict_geo",
        )
        ax.grid(axis="y", alpha=0.15)
    axes[0].set_ylabel("Recall of positive cell-weeks (%)")
    axes[0].legend(loc="upper left", frameon=False)
    _result_caption(figure, values["caption"])
    return figure


def plot_results_map(
    example: ResultsMap,
    country: BaseGeometry,
    departments: Mapping[str, BaseGeometry],
) -> Figure:
    from matplotlib.colors import FuncNorm

    figure, axes = plt.subplots(1, 2, figsize=(10, 6.0), layout="constrained")
    cells = example.cells
    west, south = cells.west.min(), cells.south.min()
    resolution = float((cells.east - cells.west).median())
    cols = np.rint((cells.west - west) / resolution).astype(int)
    rows = np.rint((cells.south - south) / resolution).astype(int)
    for ax, column, label, title in zip(
        axes,
        ("p", "n_primary"),
        ("Predicted probability", "Observed detections / cell"),
        ("Calibrated predicted probability", "Observed detections in target week"),
        strict=True,
    ):
        matrix = np.full((rows.max() + 1, cols.max() + 1), np.nan)
        matrix[rows, cols] = cells[column]
        maximum = max(1.0, float(cells[column].max()))
        norm = (
            plt.Normalize(0, 1)
            if column == "p"
            else FuncNorm((np.log1p, np.expm1), vmin=0, vmax=maximum)
        )
        mesh = ax.pcolormesh(
            west + np.arange(matrix.shape[1] + 1) * resolution,
            south + np.arange(matrix.shape[0] + 1) * resolution,
            matrix,
            cmap="cividis",
            norm=norm,
            rasterized=True,
        )
        mesh.set_clip_path(PathPatch(_country_path(country), transform=ax.transData))
        for geometry in departments.values():
            _outline(ax, geometry, color="#FFFFFF", linewidth=0.5)
        _outline(ax, country, color="#333333", linewidth=0.7)
        _map_axes(ax, country)
        ax.set_title(title, fontsize=10)
        colorbar = figure.colorbar(
            mesh, ax=ax, orientation="horizontal", shrink=0.9, pad=0.06, label=label
        )
        if column == "n_primary":
            ticks = [value for value in (0, 1, 5, 20, 100, 500, 2000) if value <= maximum]
            colorbar.set_ticks(ticks)
    figure.suptitle(f"Example target week · issue {example.summary['issue_date']} (UTC)")
    _result_caption(figure, example.summary["caption"])
    return figure


def plot_results_h2(data: ResultsFigures) -> Figure:
    values = data.summary["figures"]["h2"]
    figure, ax = plt.subplots(figsize=(8.6, 3.1), layout="constrained")
    ax.axvline(0, color="#777777", linewidth=0.9, linestyle="--")
    interval = values["interval"]
    _result_interval(ax, 0, interval, COLORS[0])
    if interval and interval.get("lower") is not None:
        for key in ("lower", "upper"):
            ax.annotate(
                f"{interval[key]:.4f}",
                (interval[key], 0),
                xytext=(0, 12),
                textcoords="offset points",
                ha="center",
                fontsize=9,
            )
    else:
        ax.text(
            0.5,
            0.5,
            values["decision"].get("reason") or "Interval unavailable",
            transform=ax.transAxes,
            ha="center",
        )
    ax.set(
        yticks=[],
        ylim=(-0.7, 0.7),
        xlabel="AP difference (retrospective − strict)",
        title="H2 · recorded 95% paired interval",
    )
    ax.grid(axis="x", alpha=0.15)
    _result_caption(figure, values["caption"])
    return figure


def write_results_figures(
    data: ResultsFigures,
    example: ResultsMap,
    country: BaseGeometry,
    departments: Mapping[str, BaseGeometry],
    *,
    artifact_directory: Path,
) -> dict[str, Path]:
    artifact_directory.mkdir(parents=True, exist_ok=True)
    makers = {
        "temporal": lambda: plot_results_ap(data, "temporal"),
        "reliability": lambda: plot_results_reliability(data),
        "per_year": lambda: plot_results_per_year(data),
        "geographic": lambda: plot_results_ap(data, "geographic"),
        "area_budget": lambda: plot_results_area_budget(data),
        "map": lambda: plot_results_map(example, country, departments),
        "h2": lambda: plot_results_h2(data),
    }
    outputs = {}
    with chart_style() as font:
        for name, make in makers.items():
            figure = make()
            try:
                output = artifact_directory / f"results_{name}.png"
                figure.savefig(
                    output,
                    dpi=300,
                    bbox_inches="tight",
                    pad_inches=0.25,
                    pil_kwargs={"optimize": True},
                    metadata={"FontFamily": font, "MathTextFontSet": STYLE["mathtext.fontset"]},
                )
                outputs[name] = output
            finally:
                plt.close(figure)
    return outputs


def build_results_artifacts(root: Path = REPO_ROOT) -> dict[str, Any]:
    data = collect_results(root)
    cells = root / "datasets" / "processed" / "cells.parquet"
    panel = root / "datasets" / "processed" / "panel" / "year=2024" / "part.parquet"
    predictions = root / "artifacts" / "predictions" / "strict" / "test.parquet"
    sidecar = predictions.with_suffix(".json")
    prediction_record = json.loads(sidecar.read_text(encoding="utf-8"))
    prediction_hash = sha256_file(predictions)
    if prediction_hash != prediction_record["prediction_sha256"]:
        raise ValueError("Map prediction checksum disagrees with its recorded provenance")
    for variant in ("strict", "strict_geo"):
        selection = data.summary["selection"][f"selection_{variant}"]
        model = selection["selected_model"]
        if prediction_record["model_ids"][f"{variant}/{model}"] != selection["model_ids"][model]:
            raise ValueError(f"Map prediction model disagrees with recorded selection: {variant}")
    example = collect_results_map(
        cells,
        panel,
        predictions,
        data.summary["figures"]["temporal"]["selected_model"],
        data.summary["figures"]["geographic"]["selected_model"],
    )
    example.summary["sources"] = {
        "cells": cells.relative_to(root).as_posix(),
        "labels": panel.relative_to(root).as_posix(),
        "predictions": predictions.relative_to(root).as_posix(),
        "prediction_provenance": sidecar.relative_to(root).as_posix(),
        "boundaries": "datasets/sources.yaml#boundaries",
    }
    for path in (cells, panel, predictions, sidecar, root / "datasets" / "sources.yaml"):
        data.summary["sources"][path.relative_to(root).as_posix()] = {"sha256": sha256_file(path)}
    data.summary["figures"]["map"] = example.summary
    outputs = write_results_figures(
        data,
        example,
        *load_boundary_geometries(Paths(root)),
        artifact_directory=root / "artifacts" / "figures",
    )
    for name, path in outputs.items():
        data.summary["figures"][name]["output"] = {
            "png": path.relative_to(root).as_posix(),
            "png_bytes": path.stat().st_size,
            "dpi": 300,
        }
    destination = root / "artifacts" / "figures" / "results_summary.json"
    destination.write_text(
        json.dumps(data.summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return data.summary


def show_results_figure(name: str, root: Path = REPO_ROOT) -> None:
    from IPython.display import Image, display

    if name not in RESULT_NAMES:
        raise ValueError("Unknown results figure")
    display(Image(filename=str(root / "artifacts" / "figures" / f"results_{name}.png"), width=900))
