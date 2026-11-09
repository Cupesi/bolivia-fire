from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import matplotlib as mpl
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.text import Text

ROOT = Path(__file__).resolve().parents[2]
EVALUATION_PATH = "artifacts/evaluation/test_strict.json"
DATASET_PATH = "artifacts/figures/dataset_summary.json"
EVALUATION_SHA256 = "31242d34ef75076678dc0675ea578c96acfadf97d68c6503415c31b54b126cec"
PDF_SHA256 = "719c0e85c97c1e6e5be612732fe36f919ed2bc56f19c7f829ae253c922a7aee4"
TITLE = "Weekly fire detections in Bolivia"
SUBTITLE = "Prioritizing the top-ranked 10% of eligible area each week"
AXIS_LABEL = "Detection-positive cell-weeks covered (%)"
SOURCE = "arXiv:2611.08539, Table 3"
BACKGROUND = "#ffffff"
TEXT = "#181818"
BASELINE = "#909090"
ACCENT = "#26834e"
WIDTH, HEIGHT, DPI = 1700, 2200, 200
MARGIN = 200
TIMES_FONT_FAMILIES = ("TeX Gyre Termes", "Times New Roman", "Liberation Serif", "Nimbus Roman")
TRACKED_MATPLOTLIB_VERSION = "3.11.2"
TRACKED_FONT_IDENTITIES = {
    "normal": {
        "family": "Times New Roman",
        "sha256": "931c5de5c70401d9324d5014c123802b4fb753000360ceb2f56c589403cd58c5",
    },
    "italic": {
        "family": "Times New Roman",
        "sha256": "e7f7a88b65188328aea58670f955022e23c712f55679524729da1e4e03c49d88",
    },
}
STYLE = {
    "font.family": "serif",
    "font.serif": list(TIMES_FONT_FAMILIES),
    "text.color": TEXT,
    "axes.labelcolor": TEXT,
    "xtick.color": TEXT,
    "svg.fonttype": "path",
    "svg.hashsalt": "bolivia-fire-summary-recall10-v2",
}


class SummaryFigureFontError(RuntimeError):
    pass


def resolve_fonts() -> dict[str, Path]:
    for family in TIMES_FONT_FAMILIES:
        fonts = {}
        for style in ("normal", "italic"):
            properties = font_manager.FontProperties(family=family, style=style, weight="normal")
            try:
                path = Path(font_manager.findfont(properties, fallback_to_default=False))
                face = font_manager.get_font(path)
            except (ValueError, OSError, RuntimeError):
                break
            face_style = face.style_name.casefold()
            italic = "italic" in face_style or "oblique" in face_style
            if (
                face.family_name.casefold() != family.casefold()
                or "bold" in face_style
                or italic != (style == "italic")
            ):
                break
            fonts[style] = path
        if len(fonts) == 2:
            return fonts
    raise SummaryFigureFontError(
        "Summary figure rendering requires regular and italic faces of TeX Gyre Termes, "
        "Times New Roman, Liberation Serif, or Nimbus Roman; "
        "install fonts-liberation or TeX Gyre Termes."
    )


def derive_week_counts(evaluation: dict[str, Any], dataset: dict[str, Any]) -> dict[str, int]:
    cells = dataset["eligible_cells"]
    non_held_out_cells = cells - dataset["held_out_cells"]
    eligible_rows = evaluation["exclusions"]["test"]["eligible"]
    test_rows = dataset["rows_per_partition"]["test"]
    eligible, eligible_remainder = divmod(eligible_rows, non_held_out_cells)
    total, total_remainder = divmod(test_rows, cells)
    assert eligible_remainder == total_remainder == 0, "Week counts must be integers"
    assert (eligible, total) == (106, 157), "Published week counts changed"
    return {
        "eligible": eligible,
        "total": total,
        "eligible_rows": eligible_rows,
        "non_held_out_cells": non_held_out_cells,
        "test_rows": test_rows,
        "national_cells": cells,
    }


def dataset_caption(population: dict[str, Any]) -> str:
    first_year = date.fromisoformat(population["first_issue"]).year
    last_year = date.fromisoformat(population["last_issue"]).year
    return f"Dataset: {population['cell_weeks']:,} cell-weeks, {first_year}–{last_year}"


def source_record(root: Path = ROOT) -> dict[str, Any]:
    evaluation_bytes = (root / EVALUATION_PATH).read_bytes()
    artifact_hash = hashlib.sha256(evaluation_bytes).hexdigest()
    if artifact_hash != EVALUATION_SHA256:
        raise ValueError("Published evaluation artifact SHA256 mismatch")
    evaluation = json.loads(evaluation_bytes)
    dataset_bytes = (root / DATASET_PATH).read_bytes()
    dataset = json.loads(dataset_bytes)
    dataset_rows = sum(dataset["rows_per_partition"].values())
    assert dataset_rows == dataset["eligible_cells"] * dataset["issue_weeks"], (
        "Dataset rows must match grid cells times issue weeks"
    )
    assert dataset_rows == 25_184_988, "Published dataset size changed"
    dataset_population = {
        "cell_weeks": dataset_rows,
        "rows_per_partition": dataset["rows_per_partition"],
        "national_cells": dataset["eligible_cells"],
        "issue_weeks": dataset["issue_weeks"],
        "first_issue": dataset["first_issue"],
        "last_issue": dataset["last_issue"],
        "json_paths": {
            "rows_per_partition": "rows_per_partition",
            "national_cells": "eligible_cells",
            "issue_weeks": "issue_weeks",
            "first_issue": "first_issue",
            "last_issue": "last_issue",
        },
    }
    assert evaluation["best_baseline"] == "train_climatology"
    assert evaluation["selected_model"] == "lightgbm_08"
    assert evaluation["variant"] == "strict" and evaluation["selection"] == "primary"
    cell_weeks = evaluation["models"]["lightgbm_08"]["calibrated"]["rows"]
    baseline_rows = evaluation["models"]["train_climatology"]["uncalibrated"]["rows"]
    assert cell_weeks == baseline_rows, "Methods must use the same cell-week population"
    table3_population = "3,127,636 eligible non-held-out cell-weeks"
    assert f"{cell_weeks:,} eligible non-held-out cell-weeks" == table3_population, (
        "Table 3 population mismatch"
    )
    definitions = (
        (
            "Historical baseline",
            "train_climatology",
            "uncalibrated",
            "Historical baseline: climatology, the detection frequency per cell and time of year "
            "in training data.",
            "Historical baseline: climatology from training years",
        ),
        (
            "Bolivia Fire",
            "lightgbm_08",
            "calibrated",
            "Bolivia Fire: calibrated LightGBM using satellite detections acquired before "
            "each week.",
            "Bolivia Fire: calibrated LightGBM using earlier detections",
        ),
    )
    transcription = {"train_climatology": 0.397, "lightgbm_08": 0.558}
    methods = []
    for label, model, calibration, definition, figure_note in definitions:
        metric = "recall_at_area_budget_10pct"
        value = evaluation["models"][model][calibration][metric]
        assert round(value, 3) == transcription[model], "Table 3 transcription mismatch"
        display = f"{100 * value:.1f}%"
        assert display == f"{100 * transcription[model]:.1f}%"
        methods.append(
            {
                "label": label,
                "json_path": f"models.{model}.{calibration}.{metric}",
                "value": value,
                "display": display,
                "definition": definition,
                "figure_note": figure_note,
            }
        )
    weeks = derive_week_counts(evaluation, dataset)
    alt_text = (
        f"Retrospective temporal test in Bolivia, 2023–2025, covering {cell_weeks:,} cell-weeks. "
        f"{dataset_caption(dataset_population)}. At a weekly budget of 10% of eligible area, "
        f"the climatology baseline covered {methods[0]['display']} and calibrated "
        f"LightGBM (Bolivia Fire) {methods[1]['display']} of detection-positive cell-weeks. "
        "Climatology uses training detection frequency by cell and time of year; "
        "LightGBM uses satellite detections acquired before each week. "
        "A cell-week is one 0.05-degree grid cell during a seven-day "
        f"target interval. {weeks['eligible']} of {weeks['total']} test weeks met the "
        "observation requirements. Source: arXiv:2611.08539v1, Table 3."
    )
    return {
        "evaluation_artifact": {"path": EVALUATION_PATH, "sha256": artifact_hash},
        "dataset_summary_artifact": {
            "path": DATASET_PATH,
            "sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        },
        "publication": {
            "citation": "arXiv:2611.08539v1, Table 3",
            "pdf_sha256": PDF_SHA256,
            "table3_recall_at_10pct": transcription,
            "table3_population": table3_population,
        },
        "methods": methods,
        "comparison_population": {
            "cell_weeks": cell_weeks,
            "json_path": "models.lightgbm_08.calibrated.rows",
            "baseline_json_path": "models.train_climatology.uncalibrated.rows",
        },
        "dataset_population": dataset_population,
        "weeks": weeks,
        "metric_definition": (
            "Pooled recall of cell-weeks with detections at the stated weekly area budget. "
            "Each week, cells are ranked within the eligible non-held-out population; "
            "the budget is 10% of its national intersection area. The boundary cell "
            "contributes only the fraction needed to reach the area limit."
        ),
        "alt_text": alt_text,
    }


def context_line(record: dict[str, Any]) -> str:
    cell_weeks = record["comparison_population"]["cell_weeks"]
    return f"Retrospective temporal test · 2023–2025 · {cell_weeks:,} cell-weeks"


def figure_layout(record: dict[str, Any]) -> Figure:
    fonts = resolve_fonts()
    with mpl.rc_context(STYLE):
        figure = Figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI, facecolor=BACKGROUND)
        FigureCanvasAgg(figure)

        def text(x: float, y: float, value: str, pixels: float, **kwargs: Any) -> Text:
            return figure.text(
                x / WIDTH,
                1 - y / HEIGHT,
                value,
                fontsize=pixels * 72 / DPI,
                va="top",
                **kwargs,
            )

        text(WIDTH / 2, MARGIN, TITLE, 76, ha="center")
        text(WIDTH / 2, 330, SUBTITLE, 48, ha="center")
        text(WIDTH / 2, 425, context_line(record), 48, ha="center", style="italic")

        # The inset keeps the endpoint ticks inside the page margins.
        axes = figure.add_axes((202 / WIDTH, (HEIGHT - 1500) / HEIGHT, 1296 / WIDTH, 900 / HEIGHT))
        axes.set_facecolor(BACKGROUND)
        axes.set_xlim(0, 100)
        axes.set_ylim(-0.35, 1.8)
        axes.set_xticks([0, 20, 40, 60, 80, 100])
        axes.tick_params(axis="x", labelsize=48 * 72 / DPI, length=5, width=0.6, pad=7)
        axes.get_xticklabels()[0].set_ha("left")
        axes.get_xticklabels()[-1].set_ha("right")
        axes.set_yticks([])
        for side in ("top", "left", "right"):
            axes.spines[side].set_visible(False)
        axes.spines["bottom"].set_color(TEXT)
        axes.spines["bottom"].set_linewidth(0.6)
        axes.set_xlabel(AXIS_LABEL, fontsize=48 * 72 / DPI, labelpad=20)

        for method, y, color, gid in zip(
            record["methods"],
            (1.25, 0.25),
            (BASELINE, ACCENT),
            ("baseline-bar", "bolivia-fire-bar"),
            strict=True,
        ):
            percentage = 100 * method["value"]
            bar = axes.barh(y, percentage, height=0.45, color=color)[0]
            bar.set_gid(gid)
            axes.text(0, y + 0.36, method["label"], fontsize=60 * 72 / DPI, va="bottom")
            axes.text(
                percentage + 2,
                y,
                method["display"],
                fontsize=68 * 72 / DPI,
                va="center",
            )

        notes = [
            text(MARGIN, y, method["figure_note"], 48, gid=gid)
            for method, y, gid in zip(
                record["methods"], (1725, 1800), ("baseline-note", "model-note"), strict=True
            )
        ]
        notes.append(
            text(
                MARGIN, 1875, dataset_caption(record["dataset_population"]), 48, gid="dataset-note"
            )
        )
        notes.append(text(MARGIN, 1950, SOURCE, 48, gid="source-note"))
        # Keep later draws on the validated faces after the style context exits.
        for item in figure.findobj(Text):
            properties = item.get_fontproperties().copy()
            properties.set_file(str(fonts[properties.get_style()]))
            item.set_fontproperties(properties)
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        for note in notes:
            bounds = note.get_window_extent(renderer)
            if "\n" in note.get_text() or bounds.x0 < MARGIN or bounds.x1 > WIDTH - MARGIN:
                raise ValueError("Caption line must fit on one line within the text block")
        return figure


def render(output_dir: Path = ROOT / "artifacts/figures", root: Path = ROOT) -> dict[str, Any]:
    record = source_record(root)
    stem = output_dir / "summary_recall10"
    with mpl.rc_context(STYLE):
        figure = figure_layout(record)
        output_dir.mkdir(parents=True, exist_ok=True)
        figure.savefig(stem.with_suffix(".png"), dpi=DPI, metadata={"Software": "Bolivia Fire"})
        figure.savefig(
            stem.with_suffix(".svg"),
            metadata={
                "Date": None,
                "Creator": "Bolivia Fire",
                "Title": TITLE,
                "Description": record["alt_text"],
            },
        )
        figure.clear()
    stem.with_suffix(".json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return record


if __name__ == "__main__":
    render()
