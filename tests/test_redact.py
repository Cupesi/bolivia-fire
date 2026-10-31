from __future__ import annotations

import io
import logging

import pytest

from bolivia_fire.redact import RedactionFilter, Redactor


@pytest.mark.parametrize(
    "message",
    [
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_SP/-70,-23,-57,-9/5",
        "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/all",
        "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={key}&x=1",
        "request failed with {key}",
    ],
)
def test_synthetic_secret_is_redacted(synthetic_key: str, message: str) -> None:
    result = Redactor([synthetic_key]).text(message.format(key=synthetic_key))
    assert synthetic_key not in result
    assert "<redacted>" in result


def test_generic_url_redaction_without_known_secret() -> None:
    redactor = Redactor()
    assert redactor.text("/api/area/csv/synthetic-key/VIIRS_SNPP_SP") == (
        "/api/area/csv/<redacted>/VIIRS_SNPP_SP"
    )
    assert redactor.text("?MAP_KEY=synthetic-key&x=1") == "?MAP_KEY=<redacted>&x=1"


def test_nested_manifest_redaction(synthetic_key: str) -> None:
    result = Redactor([synthetic_key]).sanitize({"url": synthetic_key, "items": [synthetic_key, 4]})
    assert result == {"url": "<redacted>", "items": ["<redacted>", 4]}


def test_log_arguments_and_exception_are_redacted(synthetic_key: str) -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(RedactionFilter(Redactor([synthetic_key])))
    logger = logging.Logger("synthetic-redaction")
    logger.addHandler(handler)
    try:
        raise ValueError(f"synthetic error {synthetic_key}")
    except ValueError:
        logger.exception("request %s", synthetic_key)
    assert synthetic_key not in output.getvalue()
    assert "ValueError: synthetic error <redacted>" in output.getvalue()
