from __future__ import annotations

from datetime import date

import pytest

from bolivia_fire.vegetation.request import build_request, discover_layers, year_ranges


def test_discovery_uses_returned_identifiers():
    synthetic_metadata = {
        "server-ndvi": {"Available": True, "Description": "1 km 16 days NDVI"},
        "server-evi": {"Available": True, "Description": "1 km 16 days EVI"},
        "server-quality": {"Available": True, "Description": "1 km 16 days VI Quality"},
        "server-reliability": {"Available": True, "Description": "1 km 16 days pixel reliability"},
        "server-doy": {"Available": True, "Description": "1 km 16 days composite day of the year"},
    }
    assert list(discover_layers(synthetic_metadata).values()) == list(synthetic_metadata)


def test_missing_discovered_layer_prevents_request():
    with pytest.raises(ValueError, match="layer"):
        discover_layers({})


def test_year_partitions_are_inclusive_without_gaps():
    assert year_ranges(date(2011, 12, 1), date(2013, 7, 31)) == [
        (date(2011, 12, 1), date(2011, 12, 31)),
        (date(2012, 1, 1), date(2012, 12, 31)),
        (date(2013, 1, 1), date(2013, 7, 31)),
    ]


def test_request_preserves_native_resolution_and_date_contract():
    synthetic_geo = {"type": "FeatureCollection", "features": []}
    result = build_request(date(2024, 8, 1), date(2024, 9, 30), ["server-layer"], synthetic_geo)
    assert result == {
        "task_type": "area",
        "params": {
            "dates": [{"startDate": "08-01-2024", "endDate": "09-30-2024"}],
            "layers": [{"product": "MOD13A2.061", "layer": "server-layer"}],
            "geo": synthetic_geo,
            "output": {"format": {"type": "geotiff"}, "projection": "native"},
        },
    }
