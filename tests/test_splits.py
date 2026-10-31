import pytest

from bolivia_fire.config import load_protocol
from bolivia_fire.splits import issue_dates, partition_for_issue


@pytest.mark.parametrize(
    "issue, expected",
    [
        ("2020-12-21", "train"),
        ("2020-12-28", None),
        ("2021-12-20", "val_select"),
        ("2021-12-27", None),
        ("2022-12-19", "val_calib"),
        ("2022-12-26", None),
        ("2023-01-02", "test"),
        ("2025-12-29", "test"),
    ],
)
def test_target_crossing_next_partition_is_purged(issue, expected):
    assert partition_for_issue(issue, load_protocol()) == expected


def test_issue_dates_are_protocol_mondays_and_bounds_inclusive():
    assert [
        str(day.date()) for day in issue_dates(load_protocol(), "2024-09-01", "2024-09-10")
    ] == ["2024-09-02", "2024-09-09"]
