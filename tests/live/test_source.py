from __future__ import annotations

import io
import logging
from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from bolivia_fire.firms.client import FirmsError
from bolivia_fire.firms.validation import Outcome
from bolivia_fire.redact import RedactionFilter, Redactor


def synthetic_nrt(synthetic_csv, **changes: str) -> bytes:
    frame = pd.read_csv(io.BytesIO(synthetic_csv(version="2.0NRT", **changes)), dtype="string")
    return frame.drop(columns="type").to_csv(index=False).encode()


def test_synthetic_nrt_fetch_validates_schema_version_and_redacts(
    tmp_path,
    synthetic_csv,
    synthetic_key,
) -> None:
    from bolivia_fire.live.source import LiveClient

    body = synthetic_nrt(synthetic_csv)
    with LiveClient(
        synthetic_key,
        tmp_path,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    ) as client:
        download = client.fetch_product("VIIRS_SNPP_NRT", date(2024, 9, 1), 1)
    assert download.validation.rows == 1
    assert download.validation.versions == ["2.0NRT"]
    assert synthetic_key not in download.url
    assert "/<redacted>/VIIRS_SNPP_NRT/" in download.url


@pytest.mark.parametrize("change", [{"version": "unknown"}, {"acq_time": "2460"}])
def test_synthetic_nrt_schema_drift_is_quarantined_without_secrets(
    tmp_path,
    synthetic_csv,
    synthetic_key,
    change,
) -> None:
    from bolivia_fire.live.source import LiveClient

    body = (
        synthetic_nrt(synthetic_csv)
        if not change
        else synthetic_nrt(
            synthetic_csv, **{key: value for key, value in change.items() if key != "version"}
        ).replace(b"2.0NRT", change.get("version", "2.0NRT").encode())
    )
    with (
        LiveClient(
            synthetic_key,
            tmp_path,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
        ) as client,
        pytest.raises(FirmsError) as error,
    ):
        client.fetch_product("VIIRS_SNPP_NRT", date(2024, 9, 1), 1)
    assert error.value.outcome in {Outcome.SCHEMA_DRIFT, Outcome.TRUNCATED}
    assert synthetic_key not in str(error.value)
    for path in tmp_path.iterdir():
        assert synthetic_key.encode() not in path.read_bytes()


def test_synthetic_static_source_mask_uses_375_m_great_circle_distance() -> None:
    from bolivia_fire.live.source import StaticSourceMask, parse_download

    mask = StaticSourceMask(pd.DataFrame({"latitude": [-17.5], "longitude": [-63.2]}))
    # At the equator 0.003 degrees is about 334 m; 0.004 degrees is about 445 m.
    equatorial = StaticSourceMask(pd.DataFrame({"latitude": [0.0], "longitude": [0.0]}))
    candidates = pd.DataFrame({"latitude": [0.0, 0.0], "longitude": [0.003, 0.004]})
    assert equatorial.excluded(candidates).tolist() == [True, False]
    frame = pd.DataFrame(
        {
            "latitude": ["-17.5", "-17.51"],
            "longitude": ["-63.2", "-63.2"],
            "bright_ti4": "300",
            "scan": "0.4",
            "track": "0.4",
            "acq_date": "2024-09-01",
            "acq_time": "0100",
            "satellite": "N",
            "instrument": "VIIRS",
            "confidence": "n",
            "version": "2.0NRT",
            "bright_ti5": "290",
            "frp": "10",
            "daynight": "N",
        }
    )
    parsed, removed = parse_download(
        pd.concat([frame, frame]).to_csv(index=False).encode(),
        "VIIRS_SNPP_NRT",
        box(-64, -18, -63, -17),
        mask,
    )
    assert removed == 1
    assert len(parsed) == 1
    assert parsed.primary.all()
    assert np.isclose(parsed.latitude.iloc[0], -17.51)


def test_synthetic_workflow_style_logging_redacts_urls(caplog, synthetic_key) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter(Redactor([synthetic_key])))
    stream = io.StringIO()
    handler.setStream(stream)
    logger = logging.getLogger("synthetic-live-redaction")
    logger.addHandler(handler)
    try:
        logger.warning(
            "::error::request failed https://firms.modaps.eosdis.nasa.gov/api/area/csv/%s/"
            "VIIRS_SNPP_NRT/-70,-23,-57,-9/5/2026-10-19",
            synthetic_key,
        )
    finally:
        logger.removeHandler(handler)
    assert synthetic_key not in stream.getvalue()
    assert "<redacted>" in stream.getvalue()
