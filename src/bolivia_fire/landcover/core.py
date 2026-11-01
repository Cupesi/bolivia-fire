from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

GROUPS: dict[str, tuple[int, ...]] = {
    "forest": (1, 3, 4, 6),
    "nonforest": (10, 11, 12, 13, 29, 63, 66, 77, 81, 82),
    "farming": (14, 15, 18, 21, 39, 40, 72),
    "nonvegetated": (22, 23, 24, 25, 30, 61, 68, 92),
    "water": (26, 31, 33, 34),
}
FEATURES = tuple(f"mapbiomas_{name}_fraction" for name in GROUPS)
CODE_GROUP = {code: group for group, codes in GROUPS.items() for code in codes}


def class_group(code: int) -> str | None:
    return CODE_GROUP.get(code)


def validate_groups(groups: Mapping[str, Sequence[int]]) -> None:
    if set(groups) != set(GROUPS) or any(set(groups[name]) != set(GROUPS[name]) for name in GROUPS):
        raise ValueError("Registered class groups do not match the pinned Collection 4 legend")
    codes = [code for values in groups.values() for code in values]
    if len(codes) != len(set(codes)):
        raise ValueError("Overlapping legend groups")


def summarize_areas(areas: Mapping[int, float], minimum_coverage: float = 0.95) -> dict[str, Any]:
    if not 0 < minimum_coverage <= 1:
        raise ValueError("Invalid minimum mapped area fraction")
    if any(not math.isfinite(area) or area < 0 for area in areas.values()):
        raise ValueError("Class area must be finite and nonnegative")
    total = math.fsum(areas.values())
    groups = {
        name: math.fsum(areas.get(code, 0.0) for code in codes) for name, codes in GROUPS.items()
    }
    mapped = math.fsum(groups.values())
    coverage = mapped / total if total else None
    available = mapped > 0 and coverage is not None and coverage >= minimum_coverage
    fractions = {
        f"mapbiomas_{name}_fraction": area / mapped if available else None
        for name, area in groups.items()
    }
    if available and not math.isclose(math.fsum(fractions.values()), 1, abs_tol=1e-6):
        raise ValueError("Class fractions do not sum to one")
    return {
        **fractions,
        "available": available,
        "mapped_area_fraction": coverage,
        "mapped_area_m2": mapped,
        "national_support_area_m2": total,
        "unmapped_class_area_m2": {
            code: area for code, area in areas.items() if class_group(code) is None and area > 0
        },
    }
