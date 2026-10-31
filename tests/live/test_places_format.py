from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bolivia_fire.live.places_format import decode_places, encode_places


def test_synthetic_places_dictionary_preserves_every_value() -> None:
    synthetic = {
        "cell_id": ["a", "b", "c"],
        "department": ["Santa Cruz", "Santa Cruz", None],
        "province": ["Ñuflo de Chávez", None, ""],
        "municipality": ["Concepción", "Concepción", None],
        "nearest_place_name": ["Concepción", "Concepción", ""],
        "nearest_place_feature_code": ["PPL", "PPL", None],
        "nearest_place_distance_km": [0.0, 1.2345678901, None],
        "populated_places": [[{"name": "Concepción", "population": 3}], [], []],
    }
    encoded = encode_places(synthetic)
    assert encoded["format"] == "dictionary-v1"
    assert encoded["strings"].count("Concepción") == 1
    assert encoded["municipality"][0] == encoded["nearest_place_name"][0]
    assert decode_places(json.loads(json.dumps(encoded))) == synthetic
    assert decode_places(synthetic) == synthetic
    assert encode_places(synthetic) == encoded


@pytest.mark.skipif(
    not os.environ.get("LIVE_DATA_DIR") or not os.environ.get("PLACES_REFERENCE_PATH"),
    reason="Requires a live-data checkout and the original full-grid place export",
)
def test_full_grid_places_are_lossless_and_below_size_limit() -> None:
    original = json.loads(Path(os.environ["PLACES_REFERENCE_PATH"]).read_text(encoding="utf-8"))
    path = Path(os.environ["LIVE_DATA_DIR"]) / "static/cell_places.json"
    assert len(original["cell_id"]) == 37_146
    assert path.stat().st_size < 5_000_000
    assert decode_places(json.loads(path.read_text(encoding="utf-8"))) == original
