import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree

import matplotlib as mpl
import pytest
from matplotlib import font_manager
from matplotlib.text import Text
from PIL import Image

from bolivia_fire import summary_figure

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TEXT = (
    "Weekly fire detections in Bolivia",
    "Prioritizing the top-ranked 10% of eligible area each week",
    "Historical baseline",
    "Bolivia Fire",
    "39.7%",
    "55.8%",
    "Detection-positive cell-weeks covered (%)",
    "Retrospective temporal test · 2023–2025 · 3,127,636 cell-weeks",
    "Historical baseline: climatology from training years",
    "Bolivia Fire: calibrated LightGBM using earlier detections",
    "Dataset: 25,184,988 cell-weeks, 2013–2025",
    "arXiv:2611.08539, Table 3",
)
EXCLUDED_TEXT = (
    "accuracy",
    "fires predicted",
    "prevented",
    "burned area",
    "prospective",
    "operational",
    "25.2 million",
)


@pytest.fixture(scope="module")
def times_fonts():
    try:
        return summary_figure.resolve_fonts()
    except summary_figure.SummaryFigureFontError as error:
        pytest.skip(str(error))


@pytest.fixture(scope="module")
def rendered(tmp_path_factory, times_fonts):
    first = tmp_path_factory.mktemp("summary_first")
    second = tmp_path_factory.mktemp("summary_second")
    summary_figure.render(first)
    summary_figure.render(second)
    return first, second


def test_recorded_values_and_table3_rounding():
    record = summary_figure.source_record()
    assert record["evaluation_artifact"] == {
        "path": "artifacts/evaluation/test_strict.json",
        "sha256": "31242d34ef75076678dc0675ea578c96acfadf97d68c6503415c31b54b126cec",
    }
    assert record["publication"] == {
        "citation": "arXiv:2611.08539v1, Table 3",
        "pdf_sha256": "719c0e85c97c1e6e5be612732fe36f919ed2bc56f19c7f829ae253c922a7aee4",
        "table3_recall_at_10pct": {"train_climatology": 0.397, "lightgbm_08": 0.558},
        "table3_population": "3,127,636 eligible non-held-out cell-weeks",
    }
    assert record["comparison_population"] == {
        "cell_weeks": 3_127_636,
        "json_path": "models.lightgbm_08.calibrated.rows",
        "baseline_json_path": "models.train_climatology.uncalibrated.rows",
    }
    baseline, model = record["methods"]
    assert baseline["value"] == 0.3973744526124836
    assert model["value"] == 0.5576986768319101
    assert (baseline["display"], model["display"]) == ("39.7%", "55.8%")
    assert baseline["json_path"] == (
        "models.train_climatology.uncalibrated.recall_at_area_budget_10pct"
    )
    assert model["json_path"] == "models.lightgbm_08.calibrated.recall_at_area_budget_10pct"
    assert baseline["definition"] == (
        "Historical baseline: climatology, the detection frequency per cell and time of year "
        "in training data."
    )
    assert model["definition"] == (
        "Bolivia Fire: calibrated LightGBM using satellite detections acquired before each week."
    )
    assert baseline["figure_note"] == "Historical baseline: climatology from training years"
    assert model["figure_note"] == "Bolivia Fire: calibrated LightGBM using earlier detections"


def test_week_counts_from_recorded_rows_and_cells():
    assert summary_figure.source_record()["weeks"] == {
        "eligible": 106,
        "total": 157,
        "eligible_rows": 3_127_636,
        "non_held_out_cells": 29_506,
        "test_rows": 5_831_922,
        "national_cells": 37_146,
    }
    assert 3_127_636 == 106 * (37_146 - 7_640)
    assert 5_831_922 == 157 * 37_146


def test_dataset_population_from_recorded_partitions_and_grid():
    assert summary_figure.source_record()["dataset_population"] == {
        "cell_weeks": 25_184_988,
        "rows_per_partition": {
            "purged": 111_438,
            "test": 5_831_922,
            "train": 15_452_736,
            "val_calib": 1_894_446,
            "val_select": 1_894_446,
        },
        "national_cells": 37_146,
        "issue_weeks": 678,
        "first_issue": "2013-01-07",
        "last_issue": "2025-12-29",
        "json_paths": {
            "rows_per_partition": "rows_per_partition",
            "national_cells": "eligible_cells",
            "issue_weeks": "issue_weeks",
            "first_issue": "first_issue",
            "last_issue": "last_issue",
        },
    }
    assert 111_438 + 5_831_922 + 15_452_736 + 1_894_446 + 1_894_446 == 25_184_988
    assert 37_146 * 678 == 25_184_988


@pytest.mark.parametrize(
    ("issue_weeks", "purged_rows", "message"),
    [(679, 111_438, "grid cells"), (679, 148_584, "Published dataset size")],
)
def test_synthetic_dataset_population_mismatch_is_rejected(
    tmp_path, issue_weeks, purged_rows, message
):
    synthetic_dataset = json.loads((ROOT / summary_figure.DATASET_PATH).read_text(encoding="utf-8"))
    synthetic_dataset["issue_weeks"] = issue_weeks
    synthetic_dataset["rows_per_partition"]["purged"] = purged_rows
    artifact = tmp_path / summary_figure.EVALUATION_PATH
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes((ROOT / summary_figure.EVALUATION_PATH).read_bytes())
    dataset = tmp_path / summary_figure.DATASET_PATH
    dataset.parent.mkdir(parents=True)
    dataset.write_text(json.dumps(synthetic_dataset), encoding="utf-8")
    with pytest.raises(AssertionError, match=message):
        summary_figure.source_record(tmp_path)


def test_nonintegral_week_count_is_rejected():
    synthetic_evaluation = {"exclusions": {"test": {"eligible": 3_127_637}}}
    synthetic_dataset = {
        "eligible_cells": 37_146,
        "held_out_cells": 7_640,
        "rows_per_partition": {"test": 5_831_922},
    }
    with pytest.raises(AssertionError, match="integers"):
        summary_figure.derive_week_counts(synthetic_evaluation, synthetic_dataset)


def test_changed_artifact_is_rejected(tmp_path):
    synthetic_artifact = tmp_path / "artifacts/evaluation/test_strict.json"
    synthetic_artifact.parent.mkdir(parents=True)
    synthetic_artifact.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        summary_figure.source_record(tmp_path)


@pytest.mark.parametrize(
    ("baseline_rows", "model_rows", "message"),
    [
        (3_127_635, 3_127_636, "same cell-week population"),
        (3_127_637, 3_127_637, "Table 3 population"),
    ],
)
def test_synthetic_population_mismatch_is_rejected(
    tmp_path, monkeypatch, baseline_rows, model_rows, message
):
    synthetic_evaluation = json.loads(
        (ROOT / summary_figure.EVALUATION_PATH).read_text(encoding="utf-8")
    )
    synthetic_evaluation["models"]["train_climatology"]["uncalibrated"]["rows"] = baseline_rows
    synthetic_evaluation["models"]["lightgbm_08"]["calibrated"]["rows"] = model_rows
    synthetic_bytes = json.dumps(synthetic_evaluation).encode("utf-8")
    artifact = tmp_path / summary_figure.EVALUATION_PATH
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(synthetic_bytes)
    dataset = tmp_path / summary_figure.DATASET_PATH
    dataset.parent.mkdir(parents=True)
    dataset.write_bytes((ROOT / summary_figure.DATASET_PATH).read_bytes())
    monkeypatch.setattr(
        summary_figure, "EVALUATION_SHA256", hashlib.sha256(synthetic_bytes).hexdigest()
    )
    with pytest.raises(AssertionError, match=message):
        summary_figure.source_record(tmp_path)


def test_svg_bar_lengths_share_zero_and_full_scale(rendered):
    first, _ = rendered
    svg = ElementTree.parse(first / "summary_recall10.svg")
    namespace = {"s": "http://www.w3.org/2000/svg"}
    widths = []
    for gid, expected in (
        ("baseline-bar", 0.3973744526124836),
        ("bolivia-fire-bar", 0.5576986768319101),
    ):
        path = svg.find(f".//s:g[@id='{gid}']/s:path", namespace)
        coordinates = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", path.attrib["d"])]
        left, right = min(coordinates[::2]), max(coordinates[::2])
        assert left == pytest.approx(72.72)
        width = right - left
        assert width / 466.56 == pytest.approx(expected, abs=2e-9)
        widths.append(width)
    assert widths[1] / widths[0] == pytest.approx(1.403458811117315, rel=0, abs=1e-8)
    figure = summary_figure.figure_layout(summary_figure.source_record())
    assert figure.axes[0].get_xlim() == (0, 100)
    assert list(figure.axes[0].get_xticks()) == [0, 20, 40, 60, 80, 100]
    figure.clear()


def test_required_text_and_excluded_claims():
    record = summary_figure.source_record()
    texts = (
        summary_figure.TITLE,
        summary_figure.SUBTITLE,
        *(method["label"] for method in record["methods"]),
        *(method["display"] for method in record["methods"]),
        summary_figure.AXIS_LABEL,
        summary_figure.context_line(record),
        *(method["figure_note"] for method in record["methods"]),
        summary_figure.dataset_caption(record["dataset_population"]),
        summary_figure.SOURCE,
    )
    assert texts == REQUIRED_TEXT
    content = "\n".join(texts) + json.dumps(record, ensure_ascii=False)
    for phrase in (*EXCLUDED_TEXT, "cupesí", "labs"):
        assert phrase not in content.casefold()


def test_required_svg_text_and_excluded_claims(rendered):
    first, _ = rendered
    svg = (first / "summary_recall10.svg").read_text(encoding="utf-8")
    for text in REQUIRED_TEXT:
        assert text in svg
    for phrase in EXCLUDED_TEXT:
        assert phrase not in svg.casefold()
    assert "<text" not in svg
    assert "<dc:date>" not in svg


@pytest.mark.usefixtures("times_fonts")
def test_all_text_fits_page_margins_and_mobile_minimum():
    figure = summary_figure.figure_layout(summary_figure.source_record())
    renderer = figure.canvas.get_renderer()
    texts = [text for text in figure.findobj(Text) if text.get_visible() and text.get_text()]
    assert len(texts) == 18
    for text in texts:
        assert text.get_fontsize() * figure.dpi / 72 >= 47
        bounds = text.get_window_extent(renderer)
        assert bounds.x0 >= 200, text.get_text()
        assert bounds.x1 <= 1500, text.get_text()
        assert bounds.y0 >= 200, text.get_text()
        assert bounds.y1 <= 2000, text.get_text()
        assert "\n" not in text.get_text()
    figure.clear()


@pytest.mark.usefixtures("times_fonts")
def test_paper_title_block_and_chart_style():
    figure = summary_figure.figure_layout(summary_figure.source_record())
    renderer = figure.canvas.get_renderer()
    title, subtitle, context = figure.texts[:3]
    for text in (title, subtitle, context):
        bounds = text.get_window_extent(renderer)
        assert (bounds.x0 + bounds.x1) / 2 == pytest.approx(850)
        assert text.get_fontweight() == "normal"
        assert text.get_fontfamily() == ["serif"]
    assert context.get_fontstyle() == "italic"
    assert figure.get_facecolor() == (1, 1, 1, 1)
    baseline, model = figure.axes[0].patches
    red, green, blue, _ = baseline.get_facecolor()
    assert red == green == blue
    assert model.get_facecolor() == pytest.approx((38 / 255, 131 / 255, 78 / 255, 1))
    figure.clear()


@pytest.mark.usefixtures("times_fonts")
def test_caption_lines_fit_text_block_on_one_line():
    figure = summary_figure.figure_layout(summary_figure.source_record())
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    notes = [
        text
        for text in figure.texts
        if text.get_gid() in ("baseline-note", "model-note", "dataset-note", "source-note")
    ]
    assert [text.get_text() for text in notes] == [
        "Historical baseline: climatology from training years",
        "Bolivia Fire: calibrated LightGBM using earlier detections",
        "Dataset: 25,184,988 cell-weeks, 2013–2025",
        "arXiv:2611.08539, Table 3",
    ]
    for note in notes:
        assert "\n" not in note.get_text()
        assert note.get_fontsize() * figure.dpi / 72 >= 47
        bounds = note.get_window_extent(renderer)
        assert bounds.x0 == pytest.approx(200)
        assert bounds.x1 <= 1500
        assert bounds.height <= 60
    for current, following in zip(notes, notes[1:], strict=False):
        assert current.get_window_extent(renderer).y0 > following.get_window_extent(renderer).y1
    figure.clear()


def test_exports_have_no_organization_credit(rendered):
    first, _ = rendered
    svg = (first / "summary_recall10.svg").read_text(encoding="utf-8")
    record = (first / "summary_recall10.json").read_text(encoding="utf-8")
    for name in ("Cupesí", "Labs"):
        assert name not in svg
        assert name not in record


def test_dimensions(rendered):
    first, _ = rendered
    with Image.open(first / "summary_recall10.png") as image:
        assert image.size == (1700, 2200)
        assert image.width / image.height == 612 / 792
        assert image.info["dpi"] == pytest.approx((200, 200), abs=0.01)
    svg = ElementTree.parse(first / "summary_recall10.svg").getroot()
    assert svg.attrib["viewBox"] == "0 0 612 792"


def test_alt_text():
    alt = summary_figure.source_record()["alt_text"]
    assert len(alt) < 600
    assert "Retrospective temporal test" in alt
    assert "covering 3,127,636 cell-weeks" in alt
    assert "Dataset: 25,184,988 cell-weeks, 2013–2025" in alt
    assert "Climatology uses training detection frequency by cell and time of year" in alt
    assert "LightGBM uses satellite detections acquired before each week" in alt
    assert "one 0.05-degree grid cell during a seven-day target interval" in alt
    assert "106 of 157 test weeks met the observation requirements" in alt


def test_deterministic_exports(rendered):
    first, second = rendered
    for suffix in ("json", "png", "svg"):
        name = f"summary_recall10.{suffix}"
        assert (first / name).read_bytes() == (second / name).read_bytes()
    name = "summary_recall10.json"
    assert (first / name).read_bytes() == (ROOT / "artifacts/figures" / name).read_bytes()


def test_record_matches_tracked_file():
    serialized = json.dumps(summary_figure.source_record(), ensure_ascii=False, indent=2) + "\n"
    name = "summary_recall10.json"
    assert serialized.encode("utf-8") == (ROOT / "artifacts/figures" / name).read_bytes()


def test_images_match_tracked_files(rendered):
    differences = []
    if mpl.__version__ != summary_figure.TRACKED_MATPLOTLIB_VERSION:
        differences.append(
            f"Matplotlib {mpl.__version__}; expected {summary_figure.TRACKED_MATPLOTLIB_VERSION}"
        )
    fonts = summary_figure.resolve_fonts()
    for style, expected in summary_figure.TRACKED_FONT_IDENTITIES.items():
        font_path = fonts[style]
        family = font_manager.get_font(font_path).family_name
        font_hash = hashlib.sha256(font_path.read_bytes()).hexdigest()
        if family != expected["family"] or font_hash != expected["sha256"]:
            differences.append(
                f"{style} font {family} SHA256 {font_hash}; "
                f"expected {expected['family']} SHA256 {expected['sha256']}"
            )
    if differences:
        pytest.skip("Tracked PNG/SVG rendering environment differs: " + "; ".join(differences))
    first, _ = rendered
    for suffix in ("png", "svg"):
        name = f"summary_recall10.{suffix}"
        assert (first / name).read_bytes() == (ROOT / "artifacts/figures" / name).read_bytes()


@pytest.mark.parametrize("synthetic_lookup", ["unavailable", "unrelated-family"])
def test_missing_times_fonts_raise_without_writing_files(tmp_path, monkeypatch, synthetic_lookup):
    def find_no_times_font(*args, **kwargs):
        if synthetic_lookup == "unavailable":
            raise ValueError("Synthetic font lookup failure")
        return Path(mpl.get_data_path()) / "fonts/ttf/DejaVuSerif.ttf"

    monkeypatch.setattr(font_manager, "findfont", find_no_times_font)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="requires regular and italic") as error:
        summary_figure.render(output)
    for family in ("TeX Gyre Termes", "Times New Roman", "Liberation Serif", "Nimbus Roman"):
        assert family in str(error.value)
    assert "fonts-liberation" in str(error.value)
    assert list(tmp_path.iterdir()) == []
