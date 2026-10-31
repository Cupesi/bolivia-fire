from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest
from shapely.geometry import box

from bolivia_fire.places import build_cell_places, places_columnar, read_populated_places


def test_synthetic_national_area_assignment_and_populated_places() -> None:
    cells = pd.DataFrame(
        {
            "cell_id": ["r1800c3600", "r1800c3601"],
            "west": [0.0, 0.05],
            "east": [0.05, 0.1],
            "south": [0.0, 0.0],
            "north": [0.05, 0.05],
            "longitude": [0.025, 0.075],
            "latitude": [0.025, 0.025],
            "department": ["Synthetic", "Synthetic"],
        }
    )
    places = pd.DataFrame(
        {
            "geonames_id": [1, 2, 3, 4, 5],
            "name": ["Concepción", "San José de Chiquitos", "Third", "Fourth", "Outside"],
            "feature_code": ["PPL"] * 5,
            "population": [10, 30, 20, 5, 1000],
            "longitude": [0.025, 0.03, 0.035, 0.04, 0.001],
            "latitude": [0.025] * 5,
        }
    )
    country = box(0.02, 0, 0.1, 0.05)
    provinces = [("p0", "Minor", box(0, 0, 0.03, 0.05)), ("p1", "Major", box(0.03, 0, 0.1, 0.05))]
    municipalities = [("m0", "Municipality", country)]
    result = build_cell_places(cells, country, provinces, municipalities, places)
    assert result.province.tolist() == ["Major", "Major"]
    assert result.province_national_share.iloc[0] == pytest.approx(2 / 3, abs=1e-6)
    assert result.municipality_national_share.tolist() == pytest.approx([1, 1])
    assert result.nearest_place_name.iloc[0] == "Concepción"
    assert result.nearest_place_geonames_id.iloc[0] == 1
    assert result.nearest_place_distance_km.iloc[0] == pytest.approx(0, abs=1e-10)
    assert result.nearest_place_distance_km.iloc[1] == pytest.approx(3.8918, abs=0.001)
    assert [item["geonames_id"] for item in result.populated_places.iloc[0]] == [2, 3, 1]
    assert result.populated_places.iloc[1] == []
    columnar = places_columnar(result.iloc[::-1], cells.cell_id.tolist())
    assert columnar["cell_id"] == cells.cell_id.tolist()
    assert columnar["nearest_place_name"][0] == "Concepción"
    with pytest.raises(ValueError, match="exactly"):
        places_columnar(result.iloc[:1], cells.cell_id.tolist())


def test_synthetic_geonames_utf8_and_feature_class_filter(tmp_path: Path) -> None:
    path = tmp_path / "BO.zip"
    rows = []
    for identifier, name, feature_class in [(1, "Concepción", "P"), (2, "Hill", "T")]:
        rows.append(
            "\t".join(
                [
                    str(identifier),
                    name,
                    "",
                    "",
                    "-16",
                    "-62",
                    feature_class,
                    "PPL",
                    "BO",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "42",
                    "",
                    "",
                    "America/La_Paz",
                    "2026-01-01",
                ]
            )
        )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BO.txt", "\n".join(rows).encode("utf-8"))
    result = read_populated_places(path)
    assert result.name.tolist() == ["Concepción"]
    assert result.geonames_id.tolist() == [1]
    assert result.population.tolist() == [42]


def test_synthetic_empty_populated_places_rejected() -> None:
    with pytest.raises(ValueError, match="populated"):
        build_cell_places(pd.DataFrame(), box(0, 0, 1, 1), [], [], pd.DataFrame())
