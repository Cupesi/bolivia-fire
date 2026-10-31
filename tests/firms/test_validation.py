from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from bolivia_fire.firms.validation import HEADER, Outcome, validate_csv

START = date(2024, 9, 1)


@pytest.mark.parametrize(
    ("body", "status", "outcome"),
    [
        (b"Invalid MAP_KEY.", 200, Outcome.INVALID_KEY),
        (b"Invalid MAP_KEY.", 400, Outcome.INVALID_KEY),
        (b"Exceeding allowed transaction limit", 200, Outcome.QUOTA_EXCEEDED),
        (b"Invalid API call.", 200, Outcome.ERROR_TEXT_200),
        (b"<html>server error</html>", 200, Outcome.ERROR_TEXT_200),
        (b"latitude,new_column\n", 200, Outcome.SCHEMA_DRIFT),
        (b"redirect", 302, Outcome.HTTP_ERROR),
        (b"error", 503, Outcome.HTTP_ERROR),
        (b"too many requests", 429, Outcome.QUOTA_EXCEEDED),
        (b"", 200, Outcome.ERROR_TEXT_200),
    ],
)
def test_response_classes(body: bytes, status: int, outcome: Outcome) -> None:
    assert validate_csv(body, START, 5, status=status).outcome == outcome


def test_ok_rows_and_explicit_summary(synthetic_csv: Callable[..., bytes]) -> None:
    result = validate_csv(synthetic_csv(), START, 5)
    assert result.outcome == Outcome.OK_ROWS
    assert result.rows == 1
    assert result.header == list(HEADER)
    assert result.versions == ["2.0"]
    assert result.row_validation == {
        "dates_outside_interval": 0,
        "coords_outside_bbox": 0,
        "invalid_acq_time": 0,
        "unexpected_codes": 0,
        "invalid_numeric": 0,
    }


def test_header_only_is_empty() -> None:
    result = validate_csv((",".join(HEADER) + "\n").encode(), START, 5)
    assert result.outcome == Outcome.OK_EMPTY
    assert result.rows == 0


def test_complete_record_without_trailing_newline(synthetic_csv: Callable[..., bytes]) -> None:
    result = validate_csv(synthetic_csv().rstrip(b"\n"), START, 5)
    assert result.outcome == Outcome.OK_ROWS
    assert result.rows == 1
    assert not any(result.row_validation.values())


def test_header_without_trailing_newline_is_empty() -> None:
    assert validate_csv(",".join(HEADER).encode(), START, 5).outcome == Outcome.OK_EMPTY


@pytest.mark.parametrize("change", ["short_row", "long_row", "unclosed_quote", "cut_row"])
def test_truncated(synthetic_csv: Callable[..., bytes], change: str) -> None:
    body = synthetic_csv()
    variants = {
        "short_row": body.rsplit(b",", 1)[0] + b"\n",
        "long_row": body.rstrip(b"\n") + b",extra\n",
        "unclosed_quote": body.split(b"\n")[0] + b'\n"bad\n',
        "cut_row": body[:body.index(b"290.1") + 2],
    }
    assert validate_csv(variants[change], START, 5).outcome == Outcome.TRUNCATED


@pytest.mark.parametrize("ending", [b"", b"\n"])
@pytest.mark.parametrize(
    "changes",
    [
        {"type": ""},
        {"frp": ""},
        {"bright_ti4": "3e"},
        {"latitude": "-"},
        {"acq_date": "2024-09-"},
        {"acq_time": "2400"},
        {"instrument": "VIIR"},
    ],
)
def test_partial_fields_at_eof_are_truncated(
    synthetic_csv: Callable[..., bytes], changes: dict[str, str], ending: bytes
) -> None:
    assert validate_csv(synthetic_csv(**changes).rstrip(b"\n") + ending, START, 5).outcome == (
        Outcome.TRUNCATED
    )


@pytest.mark.parametrize(
    ("changes", "counter"),
    [
        ({"acq_date": "2024-09-06"}, "dates_outside_interval"),
        ({"acq_date": "2024-08-31"}, "dates_outside_interval"),
        ({"acq_date": "invalid"}, "dates_outside_interval"),
        ({"latitude": "-24"}, "coords_outside_bbox"),
        ({"longitude": "nan"}, "coords_outside_bbox"),
        ({"acq_time": "2400"}, "invalid_acq_time"),
        ({"acq_time": "1260"}, "invalid_acq_time"),
        ({"acq_time": "-1"}, "invalid_acq_time"),
        ({"confidence": "N"}, "unexpected_codes"),
        ({"daynight": "n"}, "unexpected_codes"),
        ({"satellite": "N20"}, "unexpected_codes"),
        ({"type": "4"}, "unexpected_codes"),
        ({"frp": "nan"}, "invalid_numeric"),
    ],
)
def test_row_validation(
    synthetic_csv: Callable[..., bytes],
    changes: dict[str, str],
    counter: str,
) -> None:
    # Keep a valid final record so this exercises schema drift within the body.
    body = synthetic_csv(**changes) + synthetic_csv().split(b"\n", 1)[1]
    result = validate_csv(body, START, 5)
    assert result.outcome == Outcome.SCHEMA_DRIFT
    assert result.row_validation[counter] == 1


@pytest.mark.parametrize("time", ["0", "45", "345", "0345", "2359"])
def test_firms_time_zero_padding(synthetic_csv: Callable[..., bytes], time: str) -> None:
    assert validate_csv(synthetic_csv(acq_time=time), START, 5).ok
