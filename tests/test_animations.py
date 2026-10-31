import json
from pathlib import Path

import pandas as pd
import pytest
from matplotlib import font_manager
from PIL import Image
from shapely.geometry import box

from bolivia_fire import animations, diagrams, figures
from bolivia_fire.figures import unobserved_spans


def test_synthetic_unobserved_spans_respect_datetime_units():
    synthetic_days = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-02-01", "2024-03-01"]),
            "outage_hours": [6, 8, 12, 0.2],
        }
    )
    spans = unobserved_spans(synthetic_days)
    assert spans.to_dict("records") == [
        {"start_utc": pd.Timestamp("2024-01-01"), "end_utc": pd.Timestamp("2024-01-03")},
        {"start_utc": pd.Timestamp("2024-02-01"), "end_utc": pd.Timestamp("2024-02-02")},
    ]


def assert_media(path: Path, frames: int, publication: Path) -> None:
    with Image.open(path) as gif:
        assert frames <= gif.n_frames <= 73
        assert gif.info["loop"] == 0
        assert animations.clear_border(gif)
        for index in range(gif.n_frames - 1):
            gif.seek(index)
            assert gif.info["duration"] >= 50
            assert animations.clear_border(gif)
        gif.seek(gif.n_frames - 1)
        assert gif.info["duration"] >= 2500
    assert 0 < path.stat().st_size <= 4_000_000
    assert (publication / path.name).read_bytes() == path.read_bytes()
    assert not list(publication.glob("*.pdf"))
    assert not list(publication.glob("*.png"))


@pytest.fixture
def synthetic_weeks() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "issue_date": pd.to_datetime(["2023-01-02", "2023-08-07", "2024-01-01", "2024-08-05"]),
            "selected": [2, 8, 3, 12],
            "all_confidence": [3, 9, 5, 15],
        }
    )


def test_synthetic_weekly_reveal_and_publication(tmp_path, synthetic_weeks):
    outages = pd.DataFrame(
        {"start_utc": pd.to_datetime(["2023-08-08"]), "end_utc": pd.to_datetime(["2023-08-09"])}
    )
    path = tmp_path / "weekly.gif"
    info = animations.animate_weekly(
        synthetic_weeks, outages, path, tmp_path / "publication", steps=3
    )
    assert info["font_family"] in figures.STYLE["font.serif"]
    assert info["mathtext_fontset"] == "stix"
    assert_media(path, 3, tmp_path / "publication")


def test_animation_export_rejects_sans_serif(tmp_path, synthetic_weeks, monkeypatch):
    sans = font_manager.findfont(font_manager.FontProperties(family=["DejaVu Sans"]))
    monkeypatch.setattr(figures.font_manager, "findfont", lambda *args, **kwargs: sans)
    with pytest.raises(RuntimeError, match="serif.*DejaVu Sans"):
        animations.animate_weekly(
            synthetic_weeks, pd.DataFrame(), tmp_path / "weekly.gif", tmp_path / "publication"
        )
    assert not list(tmp_path.rglob("*.gif"))


def test_synthetic_seasonal_years(tmp_path, synthetic_weeks):
    path = tmp_path / "seasonal.gif"
    animations.animate_seasonal(synthetic_weeks, path, tmp_path / "publication")
    assert_media(path, 2, tmp_path / "publication")


@pytest.fixture
def synthetic_population() -> dict:
    return {
        "best_baseline": "climatology",
        "models": [
            {
                "model": "climatology",
                "label": "Climatology",
                "average_precision": 0.2,
                "ap_interval": {"lower": 0.1, "upper": 0.3},
                "paired_difference": 0.0,
                "paired_interval": {"lower": 0.0, "upper": 0.0},
            },
            {
                "model": "candidate",
                "label": "Candidate (calibrated)",
                "average_precision": 0.4,
                "ap_interval": {"lower": 0.3, "upper": 0.5},
                "paired_difference": 0.2,
                "paired_interval": {"lower": 0.1, "upper": 0.3},
            },
        ],
    }


@pytest.mark.parametrize("population", ["temporal", "geographic"])
def test_synthetic_ap_reveal(tmp_path, synthetic_population, population):
    path = tmp_path / f"{population}.gif"
    animations.animate_ap(synthetic_population, population, path, tmp_path / "publication")
    assert_media(path, 4, tmp_path / "publication")


def test_synthetic_reliability_bins(tmp_path):
    models = [
        {
            "label": "Synthetic calibrated candidate",
            "bins": [
                {"mean_probability": 0.1, "positive_rate": 0.15},
                {"mean_probability": 0.5, "positive_rate": 0.45},
            ],
        }
    ]
    path = tmp_path / "reliability.gif"
    animations.animate_reliability(models, path, tmp_path / "publication")
    assert_media(path, 3, tmp_path / "publication")


@pytest.fixture
def synthetic_maps() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "issue_date": date,
                "west": lon,
                "south": lat,
                "n_primary": count,
                "p": probability,
                "target_complete": complete,
            }
            for date, count, probability, complete in [
                ("2024-07-01", 1, 0.1, True),
                ("2024-07-08", 3, None, False),
            ]
            for lon, lat in [(0.0, 0.0), (0.05, 0.0), (0.0, 0.05), (0.05, 0.05)]
        ]
    )


def test_synthetic_weekly_maps_preserve_missing_estimates(tmp_path, synthetic_maps):
    path = tmp_path / "maps.gif"
    animations.animate_maps(synthetic_maps, box(0, 0, 0.1, 0.1), {}, path, tmp_path / "publication")
    assert_media(path, 2, tmp_path / "publication")


@pytest.fixture
def synthetic_renderer():
    def render(specification):
        assert specification["source"].startswith(("flowchart", "gantt"))
        return [
            Image.new("RGB", (300, 100), (index * 20, 100, 160))
            for index in range(len(specification["stages"]) + 1)
        ]

    return render


def test_synthetic_pipeline_stages(tmp_path, synthetic_renderer):
    path = tmp_path / "pipeline.gif"
    diagrams.animate_pipeline(path, tmp_path / "publication", renderer=synthetic_renderer)
    assert_media(path, 10, tmp_path / "publication")
    source = (tmp_path / "src" / "pipeline.mmd").read_text(encoding="utf-8")
    assert "archive --> benchmark --> delivery" in source
    assert "H --> I" in source


def test_synthetic_partition_calendar_and_holdout(tmp_path, synthetic_renderer):
    protocol = {
        "partitions": {
            "train": ["2013-01-07", "2020-12-31"],
            "val_select": ["2021-01-01", "2021-12-31"],
            "val_calib": ["2022-01-01", "2022-12-31"],
            "test": ["2023-01-01", "2025-12-31"],
        }
    }
    cells = pd.DataFrame({"block_id": ["b0_0", "b1_0"], "geo_holdout": [False, True]})
    calendar = pd.DataFrame(
        {"issue_date": pd.to_datetime(["2020-12-28", "2021-12-27"]), "partition": [None, None]}
    )
    path = tmp_path / "partitions.gif"
    diagrams.animate_partitions(
        protocol,
        calendar,
        cells,
        box(0, 0, 2, 1),
        path,
        tmp_path / "publication",
        renderer=synthetic_renderer,
    )
    assert_media(path, 6, tmp_path / "publication")
    source = (tmp_path / "src" / "partitions.mmd").read_text(encoding="utf-8")
    assert "Train :train, 2013-01-07, 2021-01-01" in source
    assert "2020-12-28 :purge0, 2020-12-28, 7d" in source


def test_synthetic_information_products(tmp_path, synthetic_renderer):
    path = tmp_path / "products.gif"
    diagrams.animate_products(path, tmp_path / "publication", renderer=synthetic_renderer)
    assert_media(path, 4, tmp_path / "publication")


def test_gif_timing_rejects_excessive_frame_rate(tmp_path):
    with pytest.raises(ValueError, match="50"):
        animations.write_gif([Image.new("RGB", (4, 4))], tmp_path / "fast.gif", duration_ms=100)


def test_synthetic_ui_capture_assembly(tmp_path):
    assets = tmp_path / "docs" / "assets" / "screenshots"
    assets.mkdir(parents=True)
    frame_directory = tmp_path / "captures"
    frame_directory.mkdir()
    for name in ("observed-desktop", "estimate-desktop", "table-desktop", "estimate-mobile"):
        Image.new("RGB", (40, 24), "white").save(assets / f"{name}.png")
    for index, color in enumerate(["red", "green", "blue"]):
        Image.new("RGB", (400, 240), color).save(frame_directory / f"week-{index:02}.png")
    animations.assemble_screenshots(frame_directory, tmp_path)
    with Image.open(assets / "week_navigation.gif") as gif:
        assert 60 <= gif.n_frames <= 120
        gif.seek(gif.n_frames - 1)
        assert gif.info["duration"] >= 2500
    manifest = json.loads((assets.parent / "media.json").read_text())
    assert manifest["media"]["screenshots/week_navigation.gif"]["duration_ms"] == 6100
    assert manifest["media"]["screenshots/estimate-mobile.png"]["frames"] == 1


def test_published_animation_contract():
    root = Path(__file__).resolve().parents[1]
    assets = root / "docs" / "assets"
    paths = list(assets.rglob("*.gif"))
    assert len(paths) == 14
    for path in paths:
        info = animations.media_info(path)
        assert 60 <= info["frames"] <= 120, path.name
        assert info["bytes"] <= 4_000_000, path.name
        assert info["border_clear"], path.name
        assert info["final_hold_ms"] >= 2500, path.name
        with Image.open(path) as image:
            assert image.info["loop"] == 0
    for path in (root / "artifacts" / "figures").glob("*.png"):
        with Image.open(path) as image:
            assert animations.clear_border(image), path.name
