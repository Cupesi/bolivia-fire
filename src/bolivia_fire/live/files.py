from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from bolivia_fire.firms.storage import durable_write


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    durable_write(
        temporary,
        json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode(),
    )
    os.replace(temporary, path)


def write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.part")
    frame.to_parquet(temporary, index=False, compression="zstd")
    with temporary.open("r+b") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def publish_forecast(out: Path, payload: dict[str, Any]) -> bool:
    path = out / "forecasts" / f"{payload['issue_date']}.json"
    if path.exists():
        if read_json(path) != payload:
            raise ValueError(f"Forecast {payload['issue_date']} is immutable")
        return False
    write_json(path, payload)
    return True
