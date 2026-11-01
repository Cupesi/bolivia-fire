from __future__ import annotations

from collections import defaultdict

import pytest
from shapely.geometry import box

from bolivia_fire.landcover.core import class_group, summarize_areas, validate_groups


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (6, "forest"),
        (29, "nonforest"),
        (63, "nonforest"),
        (77, "nonforest"),
        (82, "nonforest"),
        (72, "farming"),
        (92, "nonvegetated"),
        (34, "water"),
        (27, None),
        (0, None),
        (255, None),
    ],
)
def test_synthetic_legend_codes_follow_collection4(code: int, expected: str | None) -> None:
    assert class_group(code) == expected


def test_synthetic_pixel_cell_intersections_weight_fractions() -> None:
    # Synthetic planar raster: a one-unit pixel and a three-unit pixel.
    pixels = [(box(0, 0, 1, 1), 3), (box(1, 0, 4, 1), 15)]
    cells = [box(0, 0, 2, 1), box(2, 0, 4, 1)]
    results = []
    for cell in cells:
        areas: dict[int, float] = defaultdict(float)
        for pixel, code in pixels:
            areas[code] += pixel.intersection(cell).area
        results.append(summarize_areas(areas))
    assert results[0]["mapbiomas_forest_fraction"] == pytest.approx(0.5)
    assert results[0]["mapbiomas_farming_fraction"] == pytest.approx(0.5)
    assert results[1]["mapbiomas_forest_fraction"] == 0
    assert results[1]["mapbiomas_farming_fraction"] == 1


def test_synthetic_invalid_area_is_excluded_from_denominator() -> None:
    result = summarize_areas({3: 40, 15: 55, 27: 2, 255: 3})
    assert result["mapped_area_fraction"] == 0.95
    assert result["mapped_area_m2"] == 95
    assert result["national_support_area_m2"] == 100
    assert result["mapbiomas_forest_fraction"] == pytest.approx(40 / 95)
    assert result["mapbiomas_farming_fraction"] == pytest.approx(55 / 95)
    assert result["unmapped_class_area_m2"] == {27: 2, 255: 3}


@pytest.mark.parametrize("areas", [{}, {27: 100}, {3: 94.99, 0: 5.01}])
def test_synthetic_missing_or_insufficient_coverage_keeps_nulls(areas: dict[int, float]) -> None:
    result = summarize_areas(areas)
    assert result["available"] is False
    assert all(
        value is None
        for key, value in result.items()
        if key.endswith("_fraction") and key.startswith("mapbiomas_")
    )


@pytest.mark.parametrize("area", [-1, float("nan"), float("inf")])
def test_synthetic_invalid_area_is_rejected(area: float) -> None:
    with pytest.raises(ValueError, match="area"):
        summarize_areas({3: area})


def test_synthetic_changed_group_definition_is_rejected() -> None:
    with pytest.raises(ValueError, match="legend"):
        validate_groups(
            {"forest": [3], "nonforest": [29], "farming": [15], "nonvegetated": [24], "water": [34]}
        )
