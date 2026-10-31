from __future__ import annotations

import numpy as np
import pandas as pd


def test_synthetic_climatology_uses_same_week_and_department_for_unseen_cells() -> None:
    from bolivia_fire.live.static import climatology_for_issue

    climate = pd.DataFrame(
        {
            "week_index": [0, 0, 1, 1],
            "cell_id": ["a", "b", "a", "b"],
            "train_climatology_cell": [0.1, np.nan, 0.9, np.nan],
            "train_climatology_dept": [0.2, 0.2, 0.8, 0.8],
        }
    )
    cells = pd.DataFrame({"cell_id": ["b", "a"]})
    features, baseline = climatology_for_issue(cells, climate, pd.Timestamp("2026-01-05"))
    assert np.isnan(features.train_climatology_cell.iloc[0])
    assert features.train_climatology_cell.iloc[1] == 0.1
    assert baseline.tolist() == [0.2, 0.1]
