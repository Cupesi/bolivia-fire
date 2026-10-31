from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import pandas as pd
import yaml

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import sha256_file

OUTAGE_URL = "https://modaps.modaps.eosdis.nasa.gov/services/production/outages_suomi_npp.html"


class _OutageTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = []


def parse_outages(html: str) -> pd.DataFrame:
    parser = _OutageTable()
    parser.feed(html)
    records = []
    for row in parser.rows:
        if not row or not re.match(r"^\d{4}-\d{3}", row[0]):
            continue
        if len(row) != 5:
            raise ValueError("Unexpected MODAPS outage table row")
        dates = re.findall(r"\d{4}-\d{3}", row[0])
        if len(dates) not in {1, 2}:
            raise ValueError("Unexpected MODAPS outage date range")
        start_day, end_day = [
            pd.to_datetime(d, format="%Y-%j", utc=True) for d in (dates[0], dates[-1])
        ]
        times = []
        for value in row[2:4]:
            match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", value)
            if match is None:
                raise ValueError(f"Unexpected MODAPS outage UTC time: {value}")
            hour, minute, second = (int(v or 0) for v in match.groups())
            if hour > 23 or minute > 59 or second > 59:
                raise ValueError("Invalid MODAPS outage UTC time")
            times.append(pd.Timedelta(hours=hour, minutes=minute, seconds=second))
        start, end = start_day + times[0], end_day + times[1]
        # Single-day rows ending at 00:00 denote the following midnight.
        if end <= start and len(dates) == 1 and times[1] == pd.Timedelta(0):
            end += pd.Timedelta(days=1)
        if end <= start:
            raise ValueError(f"Invalid MODAPS outage interval: {row[0]}")
        records.append({"start_utc": start, "end_utc": end, "comment": row[4]})
    if not records:
        raise ValueError("MODAPS snapshot contains no outage records")
    return pd.DataFrame(records)


def load_outages(paths: Paths) -> tuple[pd.DataFrame, str]:
    sources = yaml.safe_load((paths.datasets / "sources.yaml").read_text(encoding="utf-8"))
    pin = sources["snpp_outages"]
    snapshot = (paths.root / pin["path"]).resolve()
    table = (paths.root / pin["table_path"]).resolve()
    if not snapshot.is_relative_to((paths.datasets / "raw" / "outages").resolve()):
        raise ValueError("Outage snapshot is outside the pinned outage directory")
    if not table.is_relative_to(paths.manifests.resolve()):
        raise ValueError("Outage table is outside the manifests directory")
    digest = sha256_file(snapshot)
    if digest != pin["sha256"] or sha256_file(table) != pin["table_sha256"]:
        raise ValueError("Outage snapshot or table hash mismatch")
    parsed = parse_outages(snapshot.read_text(encoding="utf-8"))
    recorded = pd.read_csv(table, keep_default_na=False)
    for name in ("start_utc", "end_utc"):
        recorded[name] = pd.to_datetime(recorded[name], utc=True)
    pd.testing.assert_frame_equal(parsed, recorded, check_dtype=False)
    return parsed, digest


def observation_days(days: pd.DataFrame, outages: pd.DataFrame) -> pd.DataFrame:
    result = days.copy()
    dates = pd.DatetimeIndex(pd.to_datetime(result.date, utc=True))
    hours = pd.Series(0.0, index=dates)
    intervals = sorted(zip(outages.start_utc, outages.end_utc, strict=True))
    merged: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in merged:
        for day in pd.date_range(start.normalize(), end.normalize(), freq="D"):
            if day in hours.index:
                overlap = max(
                    pd.Timedelta(0), min(end, day + pd.Timedelta(days=1)) - max(start, day)
                )
                hours.loc[day] += overlap.total_seconds() / 3600
    result["outage_hours"] = hours.to_numpy()
    result["observed"] = result.acquired & result.outage_hours.lt(6)
    return result
