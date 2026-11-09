from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pandas as pd

from bolivia_fire.firms.storage import durable_write
from bolivia_fire.outages import OUTAGE_URL, parse_outages


def fetch_outages(http: httpx.Client, cache: Path) -> tuple[pd.DataFrame, str]:
    with http.stream("GET", OUTAGE_URL) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError("MODAPS outage response exceeds 4 MiB")
    outages = parse_outages(body.decode("utf-8"))
    durable_write(cache / "outages.html", bytes(body))
    return outages, hashlib.sha256(body).hexdigest()
