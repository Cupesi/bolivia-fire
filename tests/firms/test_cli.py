from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from bolivia_fire import cli
from bolivia_fire.firms.client import FirmsClient


def test_cli_failure_log_exception_and_quarantine_are_sanitized(
    tmp_path: Path,
    synthetic_key: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FIRMS_MAP_KEY", synthetic_key)
    protocol = tmp_path / "configs" / "protocol.yaml"
    protocol.parent.mkdir()
    protocol.write_text(
        "target: {source_product: VIIRS_SNPP_SP}\n"
        "study_area: {acquisition_bbox: [-70, -23, -57, -9]}\n",
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if "mapkey_status" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "transaction_limit": 5000,
                    "current_transactions": 0,
                    "transaction_interval": "10 minutes",
                },
            )
        return httpx.Response(200, text=f"Invalid API call. {synthetic_key}")

    def synthetic_client(
        key: str, quarantine: Path, *, quota, transactions_per_day: int
    ) -> FirmsClient:
        return FirmsClient(
            key, quarantine, quota=quota, transactions_per_day=transactions_per_day,
            transport=httpx.MockTransport(respond),
        )

    monkeypatch.setattr(cli, "FirmsClient", synthetic_client)
    logger = logging.getLogger("bolivia_fire")
    previous_handlers, previous_level, previous_propagate = (
        list(logger.handlers),
        logger.level,
        logger.propagate,
    )
    logger.handlers = []
    try:
        assert (
            cli.main(
                [
                    "acquire",
                    "firms",
                    "--start",
                    "2024-09-01",
                    "--end",
                    "2024-09-05",
                    "--minimum-free-gb",
                    "0",
                ],
                root=tmp_path,
            )
            == 1
        )
        captured = capsys.readouterr()
        assert synthetic_key not in captured.out + captured.err
        assert "error_text_200" in captured.out
        assert "<redacted>" in captured.err
        log = tmp_path / "datasets" / ".tmp" / "acquire_firms.log"
        assert "error_text_200" in log.read_text()
        assert synthetic_key not in log.read_text()
        bodies = list((tmp_path / "datasets" / ".quarantine").glob("*.txt"))
        assert len(bodies) == 1
        assert bodies[0].read_bytes() == b"Invalid API call. <redacted>"
        assert not (tmp_path / "datasets" / "manifests" / "firms_VIIRS_SNPP_SP.jsonl").exists()
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
