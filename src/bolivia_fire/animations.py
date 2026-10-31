from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.patches import Patch, PathPatch
from PIL import GifImagePlugin, Image, ImageOps
from shapely.geometry.base import BaseGeometry

from bolivia_fire.config import REPO_ROOT, Paths, load_protocol
from bolivia_fire.detections import resolve_detection_bundle
from bolivia_fire.figures import (
    COLORS,
    STYLE,
    ResultsFigures,
    _country_path,
    _map_axes,
    _outline,
    collect_dataset,
    plot_results_area_budget,
    plot_results_h2,
    plot_results_per_year,
    plot_training_map,
    unobserved_spans,
)
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.grid import load_boundary_geometries
from bolivia_fire.outages import load_outages

MAX_GIF_BYTES = 4_000_000


def clear_border(image: Image.Image, margin: int = 12) -> bool:
    pixels = np.asarray(image.convert("RGB"))
    return all(
        np.all(edge == 255)
        for edge in (pixels[:margin], pixels[-margin:], pixels[:, :margin], pixels[:, -margin:])
    )


def media_info(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        durations = []
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            durations.append(image.info.get("duration", 0))
        return {
            "bytes": path.stat().st_size,
            "frames": len(durations),
            "width": image.width,
            "height": image.height,
            "duration_ms": sum(durations),
            "final_hold_ms": durations[-1],
            "fps": 20 if len(durations) > 1 else 0,
            "border_clear": all(
                clear_border(image.seek(i) or image) for i in range(len(durations))
            ),
        }


def write_gif(
    frames: Sequence[Image.Image],
    destination: Path,
    *,
    duration_ms: int = 50,
    final_hold_ms: int = 2500,
    smooth_frames: int = 73,
) -> dict[str, Any]:
    if duration_ms != 50:
        raise ValueError("Frame duration must be 50 ms (20 fps)")
    if final_hold_ms < 2500 or not frames:
        raise ValueError("Provide frames and a final hold of at least 2500 ms")
    destination.parent.mkdir(parents=True, exist_ok=True)
    keyframes = [ImageOps.expand(frame.convert("RGB"), border=18, fill="white") for frame in frames]
    eased = []
    for position in np.linspace(0, len(keyframes) - 1, smooth_frames):
        left = int(position)
        right = min(left + 1, len(keyframes) - 1)
        alpha = (1 - np.cos(np.pi * (position - left))) / 2
        frame = Image.blend(keyframes[left], keyframes[right], float(alpha))
        text_frame = keyframes[left if alpha < 0.5 else right]
        frame.paste(text_frame.crop((0, 0, frame.width, 90)), (0, 0))
        frame.paste(
            text_frame.crop((0, frame.height - 90, frame.width, frame.height)),
            (0, frame.height - 90),
        )
        eased.append(frame)
    sample = Image.new("RGB", (160, 90 * len(eased)), "white")
    for index, frame in enumerate(eased):
        sample.paste(frame.resize((160, 90)), (0, 90 * index))
    try:
        for colors in (256, 192, 128, 96, 64):
            palette = sample.quantize(colors=colors - 2)
            entries = (palette.getpalette() + [0] * 768)[:768]
            white, black = colors - 2, colors - 1
            entries[white * 3 : black * 3 + 3] = [255, 255, 255, 0, 0, 0]
            palette.putpalette(entries)
            quantized = [
                frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in eased
            ]
            for original, frame in zip(eased, quantized, strict=True):
                frame.putpalette(entries)
                rgb = np.asarray(original)
                indices = np.asarray(frame).copy()
                indices[np.all(rgb == 255, axis=-1)] = white
                indices[np.all(rgb == 0, axis=-1)] = black
                frame.frombytes(indices.tobytes())
                width, height = frame.size
                for bounds in (
                    (0, 0, width, 18),
                    (0, height - 18, width, height),
                    (0, 0, 18, height),
                    (width - 18, 0, width, height),
                ):
                    frame.paste(white, bounds)
            try:
                header, _ = GifImagePlugin.getheader(
                    quantized[0], info={"loop": 0, "optimize": False}
                )
                with destination.open("wb") as stream:
                    for chunk in header:
                        stream.write(chunk)
                    previous = None
                    for index, frame in enumerate(quantized):
                        values = np.asarray(frame)
                        if previous is None:
                            bounds = (0, 0, frame.width, frame.height)
                        else:
                            ys, xs = np.nonzero(values != previous)
                            bounds = (
                                (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
                                if len(xs)
                                else (0, 0, 1, 1)
                            )
                        cropped = frame.crop(bounds)
                        duration = final_hold_ms if index == len(quantized) - 1 else duration_ms
                        for chunk in GifImagePlugin.getdata(
                            cropped, offset=bounds[:2], duration=duration, disposal=1
                        ):
                            stream.write(chunk)
                        cropped.close()
                        previous = values.copy()
                    stream.write(b";")
            finally:
                palette.close()
                for frame in quantized:
                    frame.close()
            if destination.stat().st_size <= MAX_GIF_BYTES:
                break
    finally:
        sample.close()
        for frame in keyframes + eased:
            frame.close()
    result = media_info(destination)
    if result["bytes"] > MAX_GIF_BYTES:
        raise ValueError(f"GIF exceeds {MAX_GIF_BYTES} bytes: {destination.name}")
    return result


def render_animation(
    draw: Callable[[int], Figure],
    steps: int,
    destination: Path,
    publication_directory: Path,
) -> dict[str, Any]:
    if steps < 1:
        raise ValueError("An animation needs at least one step")
    publication_directory.mkdir(parents=True, exist_ok=True)
    frames = []
    with plt.rc_context(STYLE):
        try:
            for step in range(steps):
                figure = draw(step)
                try:
                    figure.set_dpi(110)
                    figure.get_layout_engine().set(w_pad=0.18, h_pad=0.18)
                    figure.canvas.draw()
                    frames.append(Image.fromarray(np.asarray(figure.canvas.buffer_rgba()).copy()))
                finally:
                    plt.close(figure)
            result = write_gif(frames, destination)
            shutil.copyfile(destination, publication_directory / destination.name)
            return result
        finally:
            for frame in frames:
                frame.close()


def animate_weekly(
    weeks: pd.DataFrame,
    outages: pd.DataFrame,
    destination: Path,
    publication_directory: Path,
    *,
    steps: int = 29,
) -> dict[str, Any]:
    weeks = weeks.sort_values("issue_date").copy()
    dates = pd.to_datetime(weeks.issue_date)
    stops = np.linspace(1, len(weeks), steps, dtype=int)

    def draw(step: int) -> Figure:
        figure, ax = plt.subplots(figsize=(9, 3.7), layout="constrained")
        stop = stops[step]
        for interval in outages.itertuples():
            ax.axvspan(
                pd.Timestamp(interval.start_utc).tz_localize(None),
                pd.Timestamp(interval.end_utc).tz_localize(None),
                color=COLORS[1],
                alpha=0.3,
            )
        ax.plot(dates[:stop], weeks.all_confidence.iloc[:stop], color="#9AB4C0", lw=1.8)
        ax.plot(dates[:stop], weeks.selected.iloc[:stop], color=COLORS[0], lw=0.8)
        ax.set(
            xlim=(dates.min(), dates.max() + pd.Timedelta(days=7)),
            ylim=(0, max(float(weeks.all_confidence.max()) * 1.12, 1)),
            xlabel="Acquisition week (UTC)",
            ylabel="Satellite detections / week",
            title="National VIIRS S-NPP detections · 2012–2025",
        )
        ax.xaxis.set_major_locator(mdates.YearLocator(2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.ticklabel_format(axis="y", style="plain")
        ax.legend(
            handles=[
                Patch(color=COLORS[0], label="Nominal + high confidence"),
                Patch(color="#9AB4C0", label="All confidence"),
                Patch(color=COLORS[1], alpha=0.3, label="Unobserved (sensor outage >= 6 h)"),
            ],
            loc="upper center",
            bbox_to_anchor=(0.5, -0.25),
            ncols=3,
            frameon=False,
        )
        return figure

    return render_animation(draw, steps, destination, publication_directory)


def animate_seasonal(
    weeks: pd.DataFrame,
    destination: Path,
    publication_directory: Path,
) -> dict[str, Any]:
    weeks = weeks.copy()
    dates = pd.to_datetime(weeks.issue_date)
    weeks["year"] = dates.dt.year
    weeks["week_index"] = np.minimum((dates.dt.day_of_year - 1) // 7, 51)
    cycle = weeks.groupby(["year", "week_index"]).selected.sum()
    years = sorted(weeks.year.unique())

    def draw(step: int) -> Figure:
        figure, ax = plt.subplots(figsize=(8.4, 4), layout="constrained")
        for index, year in enumerate(years[: step + 1]):
            values = cycle.loc[year].reindex(range(52))
            ax.plot(
                values.index,
                values,
                color=COLORS[index % len(COLORS)],
                linestyle=("-", "--", ":", "-.")[(index // len(COLORS)) % 4],
                alpha=1 if index == step else 0.55,
                lw=2 if index == step else 0.9,
                label=str(year),
            )
        ax.set(
            xlim=(0, 51),
            ylim=(0, max(float(cycle.max()) * 1.12, 1)),
            xlabel="Calendar week index (0–51)",
            ylabel="Satellite detections / week index",
            title="Annual cycle of selected national detections · 2012–2025",
        )
        ax.legend(ncols=4, loc="upper left", frameon=False)
        return figure

    return render_animation(draw, len(years), destination, publication_directory)


def animate_ap(
    population: dict[str, Any],
    name: str,
    destination: Path,
    publication_directory: Path,
) -> dict[str, Any]:
    models = population["models"]
    geographic = name == "geographic"
    label = "Geographic holdout" if geographic else "Temporal test"
    block = "one-degree spatial" if geographic else "calendar-month"

    def draw(step: int) -> Figure:
        figure, axes = plt.subplots(
            1, 2, figsize=(9.6, 4.1), layout="constrained", gridspec_kw={"width_ratios": [1.6, 1]}
        )
        for ax in axes:
            ax.set_yticks(range(len(models)), [row["label"] for row in models])
            ax.set_ylim(len(models) - 0.5, -0.6)
            ax.grid(axis="x", color="#EEEEEE", zorder=0)
        axes[1].set_yticklabels([])
        for index, row in enumerate(models[: min(step, len(models))]):
            color = COLORS[index % len(COLORS)]
            interval = row["ap_interval"]
            axes[0].plot([interval["lower"], interval["upper"]], [index, index], color=color, lw=3)
            axes[0].plot(row["average_precision"], index, "o", color=color)
            axes[0].annotate(
                f" {row['average_precision']:.3f}",
                (interval["upper"], index),
                xytext=(3, 5),
                textcoords="offset points",
                fontsize=8,
            )
        if step > len(models):
            for index, row in enumerate(models):
                interval = row["paired_interval"]
                if row["paired_difference"] is not None:
                    axes[1].plot(
                        [interval["lower"], interval["upper"]],
                        [index, index],
                        color=COLORS[index % len(COLORS)],
                        lw=3,
                    )
                    axes[1].plot(
                        row["paired_difference"], index, "o", color=COLORS[index % len(COLORS)]
                    )
        axes[0].set(
            xlim=(0, max(row["ap_interval"]["upper"] for row in models) + 0.1),
            xlabel="Average precision (AP)",
            title=f"{label} · 2023–2025",
        )
        lower = min(row["paired_interval"]["lower"] for row in models)
        upper = max(row["paired_interval"]["upper"] for row in models)
        axes[1].set(
            xlim=(min(-0.02, lower - 0.025), upper + 0.04),
            xlabel="Paired AP difference",
            title=f"Versus {population['best_baseline']}",
        )
        axes[1].axvline(0, color="#777777", linestyle="--", lw=0.8)
        figure.get_layout_engine().set(rect=(0, 0.09, 1, 0.91))
        figure.text(
            0.5,
            0.02,
            f"95% {block}-block intervals; 500 recorded replicates. "
            "Baselines original; fitted candidates calibrated.",
            ha="center",
            fontsize=8,
        )
        return figure

    return render_animation(draw, len(models) + 2, destination, publication_directory)


def animate_reliability(
    models: list[dict[str, Any]],
    destination: Path,
    publication_directory: Path,
) -> dict[str, Any]:
    bins = max(len(model["bins"]) for model in models)

    def draw(step: int) -> Figure:
        figure, axes = plt.subplots(1, 2, figsize=(8.6, 4), layout="constrained")
        maximum = max(
            row[key]
            for model in models
            for row in model["bins"]
            for key in ("mean_probability", "positive_rate")
        )
        for ax, limit in zip(axes, [max(maximum * 1.12, 0.02), 0.01], strict=True):
            ax.plot([0, limit], [0, limit], "--", color="#777777", lw=1)
            for index, model in enumerate(models):
                values = [
                    row
                    for row in model["bins"][:step]
                    if row["mean_probability"] <= limit and row["positive_rate"] <= limit
                ]
                ax.plot(
                    [row["mean_probability"] for row in values],
                    [row["positive_rate"] for row in values],
                    "o-",
                    markersize=4,
                    color=COLORS[index % len(COLORS)],
                    label=model["label"],
                )
            ax.set(
                xlim=(-limit * 0.04, limit * 1.04),
                ylim=(-limit * 0.04, limit * 1.04),
                xlabel="Mean estimated probability",
                ylabel="Observed positive cell-week share",
                title="All recorded bins" if limit > 0.01 else "Low-probability detail",
            )
            ax.grid(color="#EEEEEE")
        axes[0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), frameon=False)
        axes[1].text(
            0.04,
            0.95,
            "Remaining bins outside view",
            transform=axes[1].transAxes,
            va="top",
            fontsize=8,
        )
        figure.suptitle(f"Temporal-test reliability · {bins} equal-frequency bins")
        return figure

    return render_animation(draw, bins + 1, destination, publication_directory)


def animate_maps(
    rows: pd.DataFrame,
    country: BaseGeometry,
    departments: Mapping[str, BaseGeometry],
    destination: Path,
    publication_directory: Path,
) -> dict[str, Any]:
    rows = rows.copy()
    rows["issue_date"] = pd.to_datetime(rows.issue_date)
    dates = sorted(rows.issue_date.unique())
    west, south = rows.west.min(), rows.south.min()
    max_count = float(rows.n_primary.max())

    def draw(step: int) -> Figure:
        week = rows.loc[rows.issue_date.eq(dates[step])]
        figure, axes = plt.subplots(1, 2, figsize=(8.6, 5.2), layout="constrained")
        cols = np.rint((week.west - west) / 0.05).astype(int).to_numpy()
        indices = np.rint((week.south - south) / 0.05).astype(int).to_numpy()
        for ax, field, limit, title, units in zip(
            axes,
            ["n_primary", "p"],
            [np.log1p(max_count), 1],
            ["Observed satellite detections", "Calibrated retrospective estimate"],
            ["Detections per cell (log1p scale)", "P(≥1 detection in target week)"],
            strict=True,
        ):
            values = np.full((indices.max() + 1, cols.max() + 1), np.nan)
            series = week[field].to_numpy(dtype=float)
            values[indices, cols] = np.log1p(series) if field == "n_primary" else series
            ax.set_facecolor("#DDDDDD")
            mesh = ax.pcolormesh(
                west + np.arange(values.shape[1] + 1) * 0.05,
                south + np.arange(values.shape[0] + 1) * 0.05,
                values,
                cmap="cividis",
                vmin=0,
                vmax=max(limit, 1),
                linewidth=0,
                antialiased=False,
            )
            mesh.set_clip_path(PathPatch(_country_path(country), transform=ax.transData))
            for geometry in departments.values():
                _outline(ax, geometry, color="white", linewidth=0.4)
            _outline(ax, country, color="#333333", linewidth=0.7)
            _map_axes(ax, country)
            ax.set_title(title, fontsize=10)
            colorbar = figure.colorbar(mesh, ax=ax, shrink=0.72, pad=0.025)
            colorbar.set_label(units, fontsize=8)
            if field == "n_primary":
                ticks = np.array([0, 1, 10, 100, 1000])
                ticks = ticks[ticks <= max(max_count, 1)]
                colorbar.set_ticks(np.log1p(ticks), labels=[str(value) for value in ticks])
        complete = bool(week.target_complete.all())
        coverage = f"{week.p.notna().sum():,}/{len(week):,} cells with predictions"
        figure.suptitle(f"Issue {pd.Timestamp(dates[step]):%Y-%m-%d} · seven-day target (UTC)")
        figure.get_layout_engine().set(rect=(0, 0.07, 1, 0.93))
        figure.text(
            0.5,
            0.014,
            (
                "Complete observed target"
                if complete
                else "Incomplete target: observed counts are partial; labels are null"
            )
            + f" · {coverage}\nGray: no value. "
            "Strict outside held-out blocks; strict_geo inside.",
            ha="center",
            fontsize=8,
        )
        return figure

    return render_animation(draw, len(dates), destination, publication_directory)


def load_animation_inputs(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    paths = Paths(root)
    detections, _, _ = resolve_detection_bundle(paths, load_protocol(root))
    with duckdb.connect(
        config={"memory_limit": "512MB", "threads": "2", "TimeZone": "UTC"}
    ) as connection:
        connection.read_parquet(str(detections)).create_view("detections")
        weeks = connection.sql("""
            SELECT date_trunc('week', acq_datetime) AS issue_date,
                   count(*) FILTER (WHERE "primary") AS selected,
                   count(*) FILTER (WHERE allconf) AS all_confidence
            FROM detections WHERE in_bolivia AND year(acq_datetime) BETWEEN 2012 AND 2025
            GROUP BY issue_date ORDER BY issue_date
        """).df()
        panel_paths = sorted((paths.datasets / "processed" / "panel").glob("year=*/part.parquet"))
        connection.read_parquet(
            [str(path) for path in panel_paths], hive_partitioning=False
        ).create_view("panel")
        calendar = connection.sql(
            "SELECT DISTINCT issue_date, partition FROM panel ORDER BY issue_date"
        ).df()
        connection.read_parquet(
            str(root / "artifacts" / "predictions" / "strict" / "test.parquet")
        ).create_view("predictions")
        connection.read_parquet(str(paths.datasets / "processed" / "cells.parquet")).create_view(
            "cells"
        )
        summary = json.loads(
            (root / "artifacts" / "figures" / "results_summary.json").read_text(encoding="utf-8")
        )["figures"]
        temporal = "strict/" + summary["temporal"]["selected_model"]
        geographic = "strict_geo/" + summary["geographic"]["selected_model"]
        rows = connection.execute(
            """
            SELECT l.issue_date, c.west, c.south, l.n_primary, l.target_complete, p.p
            FROM panel l JOIN cells c USING (cell_id)
            LEFT JOIN predictions p ON p.cell_id = l.cell_id AND p.issue_date = l.issue_date
              AND p.model = CASE WHEN c.geo_holdout THEN ? ELSE ? END
            WHERE l.issue_date BETWEEN DATE '2024-07-01' AND DATE '2024-10-28'
            ORDER BY l.issue_date, l.cell_id
        """,
            [geographic, temporal],
        ).df()
    weeks["issue_date"] = pd.to_datetime(weeks.issue_date, utc=True).dt.tz_localize(None)
    index = pd.date_range("2012-01-16", "2025-12-29", freq="W-MON", name="issue_date")
    weeks = weeks.set_index("issue_date").reindex(index, fill_value=0).reset_index()
    recorded = json.loads(
        (root / "artifacts" / "figures" / "dataset_summary.json").read_text(encoding="utf-8")
    )
    for column in ("selected", "all_confidence"):
        expected = sum(
            row[column] for row in recorded["detections_per_year"] if 2012 <= row["year"] <= 2025
        )
        if int(weeks[column].sum()) != expected:
            raise ValueError(f"Weekly {column} total disagrees with recorded annual counts")
    return weeks, rows, calendar


def animate_figure(
    make: Callable[[], Figure],
    destination: Path,
    publication_directory: Path,
) -> dict[str, Any]:
    def draw(step: int) -> Figure:
        figure = make()
        for ax in figure.axes:
            for artist in [*ax.lines, *ax.collections]:
                artist.set_alpha(step / 3)
        return figure

    return render_animation(draw, 4, destination, publication_directory)


def _write_manifest(assets: Path, media: dict[str, Any], sources: dict[str, Any]) -> dict[str, Any]:
    destination = assets / "media.json"
    manifest = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {}
    manifest.setdefault("media", {}).update(media)
    manifest.setdefault("sources", {}).update(sources)
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def assemble_screenshots(frame_directory: Path, root: Path = REPO_ROOT) -> dict[str, Any]:
    frames = sorted(frame_directory.rglob("week-*.png"))
    if not frames or len({path.name for path in frames}) != len(frames):
        raise ValueError("Provide exactly one capture sequence of week-*.png frames")
    assets = root / "docs" / "assets"
    screenshots = assets / "screenshots"
    images = []
    try:
        for path in frames:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
        output = {
            "screenshots/week_navigation.gif": write_gif(
                images, screenshots / "week_navigation.gif"
            )
        }
    finally:
        for image in images:
            image.close()
    if output["screenshots/week_navigation.gif"]["duration_ms"] > 8000:
        raise ValueError("UI navigation must last at most 8 seconds")
    for name in ("observed-desktop", "estimate-desktop", "table-desktop", "estimate-mobile"):
        path = screenshots / f"{name}.png"
        with Image.open(path) as image:
            quantized = image.convert("RGB").quantize(colors=256, dither=Image.Dither.NONE)
        try:
            quantized.save(path, optimize=True)
        finally:
            quantized.close()
        output[f"screenshots/{name}.png"] = media_info(path)
        if path.stat().st_size > 400_000:
            raise ValueError(f"Screenshot exceeds 400,000 bytes: {name}")
    return _write_manifest(assets, output, {})


def build_animations(root: Path = REPO_ROOT) -> dict[str, Any]:
    from bolivia_fire.diagrams import animate_partitions, animate_pipeline, animate_products

    weeks, rows, calendar = load_animation_inputs(root)
    summary_path = root / "artifacts" / "figures" / "results_summary.json"
    results = ResultsFigures(json.loads(summary_path.read_text(encoding="utf-8")))
    figures = results.summary["figures"]
    _, day_path, _ = resolve_detection_bundle(Paths(root), load_protocol(root))
    outages = unobserved_spans(pd.read_parquet(day_path))
    country, departments = load_boundary_geometries(Paths(root))
    cells = pd.read_parquet(root / "datasets" / "processed" / "cells.parquet")
    detection_path, day_path, _ = resolve_detection_bundle(Paths(root), load_protocol(root))
    all_outages, _ = load_outages(Paths(root))
    dataset = collect_dataset(
        cells,
        sorted((root / "datasets" / "processed" / "panel").glob("year=*/part.parquet")),
        day_path,
        detection_path,
        all_outages,
        load_protocol(root),
    )
    assets = root / "docs" / "assets"
    charts, diagrams = assets / "charts", assets / "diagrams"
    publication = root / "data" / "figures"
    output = {}
    jobs = [
        (
            "charts/dataset_map.gif",
            lambda: animate_figure(
                lambda: plot_training_map(dataset, country, departments),
                charts / "dataset_map.gif",
                publication,
            ),
        ),
        (
            "charts/per_year_ap.gif",
            lambda: animate_figure(
                lambda: plot_results_per_year(results), charts / "per_year_ap.gif", publication
            ),
        ),
        (
            "charts/area_budget.gif",
            lambda: animate_figure(
                lambda: plot_results_area_budget(results), charts / "area_budget.gif", publication
            ),
        ),
        (
            "charts/h2_interval.gif",
            lambda: animate_figure(
                lambda: plot_results_h2(results), charts / "h2_interval.gif", publication
            ),
        ),
        (
            "charts/national_weekly.gif",
            lambda: animate_weekly(weeks, outages, charts / "national_weekly.gif", publication),
        ),
        (
            "charts/seasonal_cycle.gif",
            lambda: animate_seasonal(weeks, charts / "seasonal_cycle.gif", publication),
        ),
        (
            "charts/fire_season_2024.gif",
            lambda: animate_maps(
                rows, country, departments, charts / "fire_season_2024.gif", publication
            ),
        ),
        (
            "charts/temporal_ap.gif",
            lambda: animate_ap(
                figures["temporal"], "temporal", charts / "temporal_ap.gif", publication
            ),
        ),
        (
            "charts/reliability.gif",
            lambda: animate_reliability(
                figures["reliability"]["models"], charts / "reliability.gif", publication
            ),
        ),
        (
            "charts/geographic_ap.gif",
            lambda: animate_ap(
                figures["geographic"], "geographic", charts / "geographic_ap.gif", publication
            ),
        ),
        ("diagrams/pipeline.gif", lambda: animate_pipeline(diagrams / "pipeline.gif", publication)),
        (
            "diagrams/partitions.gif",
            lambda: animate_partitions(
                load_protocol(root),
                calendar,
                cells,
                country,
                diagrams / "partitions.gif",
                publication,
            ),
        ),
        (
            "diagrams/information_products.gif",
            lambda: animate_products(diagrams / "information_products.gif", publication),
        ),
    ]
    for name, make in jobs:
        output[name] = make()
    sources = [
        summary_path,
        root / "artifacts" / "figures" / "dataset_summary.json",
        root / "artifacts" / "evaluation" / "test_strict.json",
        root / "configs" / "protocol.yaml",
        root / "artifacts" / "predictions" / "strict" / "test.parquet",
        root / "datasets" / "processed" / "cells.parquet",
        root / "datasets" / "manifests" / "snpp_outages.csv",
    ]
    return _write_manifest(
        assets,
        output,
        {path.relative_to(root).as_posix(): {"sha256": sha256_file(path)} for path in sources},
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render media from existing local artifacts")
    parser.add_argument("--screenshot-frames", type=Path)
    args = parser.parse_args()
    result = (
        assemble_screenshots(args.screenshot_frames)
        if args.screenshot_frames
        else build_animations()
    )
    print(json.dumps(result, indent=2))
