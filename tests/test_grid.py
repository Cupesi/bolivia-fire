import numpy as np
from hypothesis import given
from hypothesis import strategies as st
from shapely.geometry import box

from bolivia_fire.config import load_protocol
from bolivia_fire.grid import build_cells, cell_id, coordinate_indices, holdout_blocks


def test_negative_edges_and_float_noise():
    assert cell_id(-63.2, -17.5) == "r1450c2336"
    assert cell_id(-63.20000000000002, -17.50000000000001) == "r1450c2336"
    assert cell_id(-63.200001, -17.500001) == "r1449c2335"


@given(
    st.integers(-1399, -1141),
    st.integers(-459, -181),
    st.floats(0.00001, 0.04999, allow_nan=False),
    st.floats(0.00001, 0.04999, allow_nan=False),
)
def test_points_are_inside_their_half_open_cells(lon_step, lat_step, dx, dy):
    lon, lat = lon_step / 20 + dx, lat_step / 20 + dy
    rows, cols = coordinate_indices(np.array([lon]), np.array([lat]))
    west, south = cols[0] / 20 - 180, rows[0] / 20 - 90
    assert west <= lon < west + 0.05
    assert south <= lat < south + 0.05


def test_synthetic_grid_positive_area_department_and_geodesic_area():
    country = box(-63.025, -18.025, -62.975, -17.975)
    cells = build_cells(country, {"Santa Cruz": box(-64, -19, -62, -17)}, load_protocol())
    assert len(cells) == 4
    assert cells.santa_cruz.all()
    assert cells.national_fraction.between(0.24, 0.26).all()
    assert 28 < cells.national_area_km2.sum() < 30
    assert set(cells.block_id) == {"b-64_-19", "b-63_-19", "b-64_-18", "b-63_-18"}


def test_geographic_holdout_is_deterministic_and_twenty_percent():
    blocks = [f"b{i}_0" for i in range(100)]
    assert holdout_blocks(blocks, 20261031, 0.2) == holdout_blocks(blocks[::-1], 20261031, 0.2)
    assert len(holdout_blocks(blocks, 20261031, 0.2)) == 20


def test_synthetic_department_uses_largest_national_share():
    country = box(-63, -18, -62.95, -17.95)
    cells = build_cells(
        country,
        {
            "Other": box(-63, -18, -62.98, -17.95),
            "Santa Cruz": box(-62.98, -18, -62.95, -17.95),
        },
        load_protocol(),
    )
    assert len(cells) == 1
    assert cells.department.tolist() == ["Santa Cruz"]
    assert cells.santa_cruz.all()
