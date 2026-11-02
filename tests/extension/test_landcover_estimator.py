from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml
from pyproj import Geod
from shapely.geometry import Point, box, shape

from bolivia_fire.config import REPO_ROOT, Paths
from bolivia_fire.landcover.core import FEATURES, GROUPS
from bolivia_fire.landcover.raster import BANDS, raster_year
from tests.landcover.test_usage import synthetic_ledger


def test_synthetic_extraction_quantifies_unequal_area_and_border_approximation(tmp_path):
    west, south, step = -63.0, -17.55, 0.005
    native = np.full((10, 10), 15)
    native[:, :5] = 3
    native[9, 9] = 27
    country = box(west, south, west + 0.0499, south + 0.05)
    geod = Geod(ellps="WGS84")
    national_area = abs(geod.geometry_area_perimeter(country)[0])
    centers = [
        [Point(west + (col + 0.5) * step, south + (row + 0.5) * step) for col in range(10)]
        for row in range(10)
    ]
    reductions = []

    class SyntheticImage:
        def __init__(self, values):
            self.values = values

        def select(self, name):
            assert name == "classification_2012"
            return self

        def unmask(self, value, same_footprint):
            assert value == 0 and same_footprint is False
            return self

        def remap(self, codes, values, default):
            assert set(values) == {1} and default == 0
            return SyntheticImage(np.isin(self.values, codes).astype(float))

        def eq(self, value):
            return SyntheticImage((self.values == value).astype(float))

        def rename(self, name):
            return SyntheticImage({name: self.values})

        def toFloat(self):
            return self

        def clip(self, geometry):
            # Model native pixel center masking, independently of geodesic intersections.
            polygon = shape(geometry)
            mask = np.array([[polygon.covers(point) for point in row] for row in centers])
            return SyntheticImage({name: values * mask for name, values in self.values.items()})

        def reduceResolution(self, *, reducer, bestEffort, maxPixels):
            assert reducer == "synthetic arithmetic mean"
            assert bestEffort is False and maxPixels == 65536
            reductions.append(True)
            return SyntheticImage({name: values.mean() for name, values in self.values.items()})

        def reproject(self, *, crs, crsTransform):
            assert crs == "EPSG:4326"
            assert crsTransform == pytest.approx([0.05, 0, west, 0, -0.05, south + 0.05])
            return self

    ee = MagicMock()
    ee.Image.side_effect = lambda asset: SyntheticImage(native)
    ee.Image.cat.side_effect = lambda images: SyntheticImage(
        {name: values for image in images for name, values in image.values.items()}
    )
    ee.Geometry.side_effect = lambda geometry, **kwargs: geometry
    ee.Reducer.mean.return_value = "synthetic arithmetic mean"

    def synthetic_compute(request):
        result = np.zeros((1, 1), dtype=[(name, "f8") for name in BANDS])
        for name, value in request["expression"].values.items():
            result[name] = value
        return result

    ee.data.computePixels.side_effect = synthetic_compute

    @contextmanager
    def synthetic_profile(*, destination):
        yield
        destination.write(" EECU seconds PeakMem Count Description\n 0.001 1k 1 synthetic\n")

    ee.profilePrinting.side_effect = synthetic_profile
    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    session = SimpleNamespace(
        ee=ee,
        paths=paths,
        ledger=synthetic_ledger(tmp_path / "synthetic_usage.json"),
        resources=SimpleNamespace(check=lambda: None, peak_rss_bytes=0, minimum_free_bytes=0),
        project="synthetic-oracle-project",
        transform=[step, 0, west, 0, -step, south + 0.05],
    )
    cells = pd.DataFrame(
        {
            "cell_id": ["synthetic-border"],
            "west": [west],
            "east": [west + 0.05],
            "south": [south],
            "north": [south + 0.05],
            "national_area_km2": [national_area / 1_000_000],
        }
    )
    frame, _, report = raster_year(session, cells, country, 2012, 0.1)
    assert reductions == [True]
    assert report["crs"] == "EPSG:4326"
    assert session.ledger._load()["batches"][0]["status"] == "measured"
    areas = {name: 0.0 for name in GROUPS}
    total = 0.0
    native_areas = []
    border_areas = []
    for row in range(10):
        for col in range(10):
            pixel = box(
                west + col * step,
                south + row * step,
                west + (col + 1) * step,
                south + (row + 1) * step,
            )
            full_area = abs(geod.geometry_area_perimeter(pixel)[0])
            clipped_area = abs(geod.geometry_area_perimeter(pixel.intersection(country))[0])
            native_areas.append(full_area)
            if col == 9:
                border_areas.append(clipped_area / full_area)
            total += clipped_area
            for name, codes in GROUPS.items():
                if native[row, col] in codes:
                    areas[name] += clipped_area
    assert max(native_areas) > min(native_areas) * 1.0001
    assert all(0 < fraction < 1 for fraction in border_areas)
    mapped = sum(areas.values())
    oracle = np.array(list(areas.values())) / mapped
    production = frame.loc[0, list(FEATURES)].to_numpy(dtype=float)
    fraction_error = float(np.max(abs(production - oracle)))
    coverage_error = abs(float(frame.loc[0, "mapped_area_fraction"]) - mapped / total)
    assert production[0] == pytest.approx(50 / 99)
    assert 0.0008 < fraction_error < 0.0011
    assert 0.0001 < coverage_error < 0.0003
    print(
        f"synthetic extraction max_fraction_error={fraction_error:.12g} "
        f"coverage_error={coverage_error:.12g}"
    )


def test_registered_estimator_cites_observed_polygon_pilot_evidence():
    amendment = yaml.safe_load(
        (REPO_ROOT / "configs/extension_amendment_landcover.yaml").read_text("utf-8")
    )
    estimator = amendment["estimator"]
    assert estimator["operation"] == "binary_class_masks_reduceResolution_mean"
    assert estimator["crs"] == "EPSG:4326"
    evidence = estimator["acceptance_evidence"]
    assert evidence["source"] == "datasets/processed/landcover/source.json"
    assert evidence["manifest"] == "datasets/manifests/mapbiomas_raster.jsonl"
    assert evidence["cells"] == 1000
    assert evidence["fraction_max_absolute_error"] == 0.0006533796633643452
    assert evidence["coverage_max_absolute_error"] == 0.0002451674139329896
    assert evidence["absolute_tolerance"] == 0.01
    assert evidence["scope"] == "observed_polygon_pilot_only_not_a_national_error_bound"
