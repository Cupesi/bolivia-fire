from __future__ import annotations

import pandas as pd
import pyarrow as pa

from bolivia_fire.release.validation import check_panel_table, check_schema


def test_synthetic_schema_rejects_wrong_types_and_accepts_timestamp_precision() -> None:
    columns = {"cell_id": {"dtype": "string"}, "issue_date": {"dtype": "timestamp"},
               "y_primary": {"dtype": "int8", "nullable": True}}
    schema = pa.schema([("cell_id", pa.large_string()), ("issue_date", pa.timestamp("us")),
                        ("y_primary", pa.int8())])
    assert check_schema(schema, columns) == []
    assert check_schema(pa.schema([("cell_id", pa.int32())]), columns) == [
        "cell_id: expected string, found int32", "missing issue_date", "missing y_primary"]


def test_synthetic_panel_checks_assignments_and_observation_nullness() -> None:
    protocol = {"partitions": {"train": ["2013-01-07", "2020-12-31"],
                               "val_select": ["2021-01-01", "2021-12-31"],
                               "val_calib": ["2022-01-01", "2022-12-31"],
                               "test": ["2023-01-01", "2025-12-31"]},
                "target": {"horizon_days": 7}}
    synthetic = pd.DataFrame({"cell_id": ["a", "b"],
                              "issue_date": pd.to_datetime(["2022-12-26", "2023-01-02"]),
                              "partition": [None, "test"], "target_complete": [True, False],
                              "primary_missing": [False, True], "allconf_missing": [False, True]})
    result = check_panel_table(synthetic, protocol)
    assert result["passed"]
    wrong_year = check_panel_table(synthetic, protocol, expected_year=2021)
    assert wrong_year["year_errors"] == 2
    assert not wrong_year["passed"]
    synthetic.loc[1, "primary_missing"] = False
    result = check_panel_table(synthetic, protocol)
    assert result["nullness_errors"] == 1
    assert not result["passed"]
