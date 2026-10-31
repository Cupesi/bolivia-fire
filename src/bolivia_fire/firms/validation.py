from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

HEADER = (
    "latitude",
    "longitude",
    "bright_ti4",
    "scan",
    "track",
    "acq_date",
    "acq_time",
    "satellite",
    "instrument",
    "confidence",
    "version",
    "bright_ti5",
    "frp",
    "daynight",
    "type",
)
BBOX = (-70.0, -23.0, -57.0, -9.0)
type BBox = tuple[float, float, float, float]


class Outcome(StrEnum):
    OK_ROWS = "ok_rows"
    OK_EMPTY = "ok_empty"
    ERROR_TEXT_200 = "error_text_200"
    INVALID_KEY = "invalid_key"
    QUOTA_EXCEEDED = "quota_exceeded"
    TRUNCATED = "truncated"
    SCHEMA_DRIFT = "schema_drift"
    HTTP_ERROR = "http_error"


@dataclass
class Validation:
    outcome: Outcome
    rows: int = 0
    header: list[str] = field(default_factory=list)
    row_validation: dict[str, int] = field(
        default_factory=lambda: {
            "dates_outside_interval": 0,
            "coords_outside_bbox": 0,
            "invalid_acq_time": 0,
            "unexpected_codes": 0,
            "invalid_numeric": 0,
        }
    )
    versions: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome in (Outcome.OK_ROWS, Outcome.OK_EMPTY)


def error_outcome(body: bytes) -> Outcome | None:
    text = body[:16384].decode("utf-8", errors="replace").lower()
    if "invalid map_key" in text or "invalid map key" in text:
        return Outcome.INVALID_KEY
    if "exceeding allowed transaction" in text or "quota exceeded" in text:
        return Outcome.QUOTA_EXCEEDED
    return None


def validate_csv(
    body: bytes,
    start: date,
    day_range: int,
    bbox: BBox = BBOX,
    status: int = 200,
) -> Validation:
    problem = error_outcome(body)
    if problem:
        return Validation(problem)
    if status != 200:
        return Validation(Outcome.QUOTA_EXCEEDED if status == 429 else Outcome.HTTP_ERROR)
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return Validation(Outcome.ERROR_TEXT_200)
    if "," not in text.partition("\n")[0]:
        return Validation(Outcome.ERROR_TEXT_200)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader)
        result = Validation(Outcome.OK_EMPTY, header=header)
        if tuple(header) != HEADER:
            result.outcome = Outcome.SCHEMA_DRIFT
            return result
        versions: set[str] = set()
        last_row_valid = True
        end = start + timedelta(days=day_range)
        west, south, east, north = bbox
        for row in reader:
            if len(row) != len(HEADER):
                result.outcome = Outcome.TRUNCATED
                return result
            result.rows += 1
            values = dict(zip(HEADER, row, strict=True))
            versions.add(values["version"])
            try:
                observed = date.fromisoformat(values["acq_date"])
                valid_date = start <= observed < end
            except ValueError:
                valid_date = False
            result.row_validation["dates_outside_interval"] += not valid_date
            try:
                lat, lon = float(values["latitude"]), float(values["longitude"])
                valid_coords = south <= lat <= north and west <= lon <= east
            except ValueError:
                valid_coords = False
            result.row_validation["coords_outside_bbox"] += not valid_coords
            # FIRMS CSV may omit the leading zero in times before 10:00 UTC.
            time = values["acq_time"]
            valid_time = bool(re.fullmatch(r"[0-9]{1,4}", time))
            if valid_time:
                valid_time = int(time.zfill(4)[:2]) < 24 and int(time.zfill(4)[2:]) < 60
            result.row_validation["invalid_acq_time"] += not valid_time
            valid_codes = (
                values["confidence"] in {"l", "n", "h"}
                and values["daynight"] in {"D", "N"}
                and values["type"] in {"0", "1", "2", "3"}
                and values["satellite"] == "N"
                and values["instrument"] == "VIIRS"
                and bool(values["version"])
            )
            result.row_validation["unexpected_codes"] += not valid_codes
            try:
                numbers = [
                    float(values[name])
                    for name in (
                        "bright_ti4",
                        "bright_ti5",
                        "scan",
                        "track",
                        "frp",
                    )
                ]
                valid_numeric = all(math.isfinite(number) and number >= 0 for number in numbers)
            except ValueError:
                valid_numeric = False
            result.row_validation["invalid_numeric"] += not valid_numeric
            last_row_valid = all(
                (valid_date, valid_coords, valid_time, valid_codes, valid_numeric)
            )
        result.versions = sorted(versions)
        if not last_row_valid:
            result.outcome = Outcome.TRUNCATED
        elif any(result.row_validation.values()):
            result.outcome = Outcome.SCHEMA_DRIFT
        elif result.rows:
            result.outcome = Outcome.OK_ROWS
        return result
    except (csv.Error, StopIteration):
        return Validation(Outcome.TRUNCATED)
