from __future__ import annotations

import gzip
import json
import traceback
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest

from bolivia_fire.firms.client import FirmsClient, FirmsError, retry_after
from bolivia_fire.firms.validation import HEADER, Outcome

START = date(2024, 9, 1)


@pytest.mark.parametrize("compressed", [False, True])
def test_streamed_body_validates_eof_and_transfer_length(
    tmp_path: Path, synthetic_key: str, synthetic_csv: Callable[..., bytes], compressed: bool
) -> None:
    body = synthetic_csv().rstrip(b"\n")
    wire = gzip.compress(body) if compressed else body
    headers = {"Content-Encoding": "gzip", "Content-Length": str(len(wire))} if compressed else {}
    with FirmsClient(
        synthetic_key, tmp_path,
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers=headers, stream=httpx.ByteStream(wire)
        )),
    ) as client:
        assert client.fetch(START, 5).body == body


@pytest.mark.parametrize("extra_bytes", [0, 1, -1])
def test_content_length_matches_received_bytes(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
    extra_bytes: int,
) -> None:
    body = synthetic_csv()
    with FirmsClient(
        synthetic_key,
        tmp_path,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=body, headers={"Content-Length": str(len(body) + extra_bytes)}
            )
        ),
    ) as client:
        if extra_bytes:
            with pytest.raises(FirmsError) as caught:
                client.fetch(START, 5)
            assert caught.value.outcome == Outcome.TRUNCATED
            assert len(list(tmp_path.glob("firms_truncated_*.txt"))) == 1
        else:
            assert client.fetch(START, 5).body == body


@pytest.mark.parametrize("transactions_per_day", [None, 3])
def test_area_retries_reserve_configured_transaction_cost(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
    synthetic_clock,
    transactions_per_day: int | None,
) -> None:
    from bolivia_fire.firms.quota import Quota

    responses = [
        httpx.Response(200, json={
            "transaction_limit": 5000, "current_transactions": 0,
            "transaction_interval": "10 minutes",
        }),
        httpx.Response(503, text="synthetic outage"),
        httpx.Response(200, content=synthetic_csv()),
    ]
    quota = Quota(tmp_path, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    options = {} if transactions_per_day is None else {
        "transactions_per_day": transactions_per_day
    }
    with FirmsClient(
        synthetic_key, tmp_path / "quarantine", quota=quota,
        transport=httpx.MockTransport(lambda request: responses.pop(0)),
        clock=synthetic_clock.time, sleep=synthetic_clock.sleep,
        jitter=lambda low, high: 0, **options,
    ) as client:
        assert client.fetch(START, 5).validation.ok
        assert client.data_requests == 2
    assert [event[1] for event in json.loads(quota.path.read_text())["events"]] == (
        [1, 10, 10] if transactions_per_day is None else [1, 15, 15]
    )


class SyntheticDisconnectStream(httpx.SyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __iter__(self) -> Iterator[bytes]:
        yield self.body
        raise httpx.RemoteProtocolError(self.body.decode())


def test_partial_disconnect_is_quarantined_before_retry(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
    synthetic_clock,
) -> None:
    partial = f"synthetic partial {synthetic_key}".encode()
    responses = [
        httpx.Response(200, stream=SyntheticDisconnectStream(partial)),
        httpx.Response(200, content=synthetic_csv()),
    ]
    with FirmsClient(
        synthetic_key,
        tmp_path,
        transport=httpx.MockTransport(lambda request: responses.pop(0)),
        sleep=synthetic_clock.sleep,
        jitter=lambda low, high: high,
    ) as client:
        assert client.fetch(START, 5).validation.rows == 1
        assert client.data_requests == 2
    assert synthetic_clock.sleeps == [1]
    bodies = list(tmp_path.glob("firms_truncated_*.txt"))
    assert len(bodies) == 1
    assert bodies[0].read_bytes() == b"synthetic partial <redacted>"


def test_exhausted_partial_disconnect_is_truncated(tmp_path: Path, synthetic_key: str) -> None:
    with FirmsClient(
        synthetic_key,
        tmp_path,
        retries=0,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=SyntheticDisconnectStream(b"synthetic partial")
            ),
        ),
    ) as client:
        with pytest.raises(FirmsError) as caught:
            client.fetch(START, 5)
        assert caught.value.outcome == Outcome.TRUNCATED
        assert client.data_requests == 1


def test_5xx_retries_are_bounded(tmp_path: Path, synthetic_key: str, synthetic_clock) -> None:
    with FirmsClient(
        synthetic_key,
        tmp_path,
        retries=2,
        sleep=synthetic_clock.sleep,
        jitter=lambda low, high: high,
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="synthetic outage")),
    ) as client:
        with pytest.raises(FirmsError) as caught:
            client.fetch(START, 5)
        assert caught.value.outcome == Outcome.HTTP_ERROR
        assert client.data_requests == 3
    assert synthetic_clock.sleeps == [1, 2]
    assert len(list(tmp_path.glob("firms_http_error_*.txt"))) == 3


@pytest.mark.parametrize(
    ("body", "status", "outcome"),
    [
        (b"Invalid MAP_KEY.", 200, Outcome.INVALID_KEY),
        (b"Invalid MAP_KEY.", 503, Outcome.INVALID_KEY),
        (b"Invalid API call.", 200, Outcome.ERROR_TEXT_200),
        (b"Invalid API call.", 503, Outcome.HTTP_ERROR),
        (b"new,schema\n", 200, Outcome.SCHEMA_DRIFT),
        ((",".join(HEADER) + "\n-1\n").encode(), 200, Outcome.TRUNCATED),
        (b"redirect", 302, Outcome.HTTP_ERROR),
        (b"not found", 404, Outcome.HTTP_ERROR),
    ],
)
def test_permanent_errors_stop_and_quarantine(
    tmp_path: Path,
    synthetic_key: str,
    body: bytes,
    status: int,
    outcome: Outcome,
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, content=body + b" " + synthetic_key.encode())

    with FirmsClient(synthetic_key, tmp_path, transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(FirmsError) as caught:
            client.fetch(START, 5)
        assert caught.value.outcome == outcome
        assert len(calls) == 1
        assert synthetic_key not in str(caught.value)
        assert synthetic_key not in "".join(traceback.format_exception(caught.value))
        assert synthetic_key not in "".join(path.read_text() for path in tmp_path.glob("*.txt"))


def test_retries_honor_retry_after(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
    synthetic_clock,
) -> None:
    responses = [
        httpx.Response(503, content=b"unavailable", headers={"Retry-After": "7"}),
        httpx.Response(200, content=synthetic_csv()),
    ]
    with FirmsClient(
        synthetic_key,
        tmp_path,
        transport=httpx.MockTransport(lambda request: responses.pop(0)),
        clock=synthetic_clock.time,
        sleep=synthetic_clock.sleep,
        jitter=lambda low, high: high,
    ) as client:
        assert client.fetch(START, 5).validation.rows == 1
        assert client.data_requests == 2
    assert synthetic_clock.sleeps == [7]


@pytest.mark.parametrize(
    "exception", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
def test_transport_retries_are_bounded_and_sanitized(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_clock,
    exception,
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise exception(f"failed {request.url}")

    with FirmsClient(
        synthetic_key,
        tmp_path,
        transport=httpx.MockTransport(respond),
        retries=2,
        sleep=synthetic_clock.sleep,
        jitter=lambda low, high: high,
    ) as client:
        with pytest.raises(FirmsError) as caught:
            client.fetch(START, 5)
        assert len(calls) == 3
        assert synthetic_clock.sleeps == [1, 2]
        assert synthetic_key not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("status", [200, 429])
def test_quota_response_waits_window(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_csv: Callable[..., bytes],
    synthetic_clock,
    status: int,
) -> None:
    responses = [
        httpx.Response(status, content=b"Exceeding allowed transaction limit"),
        httpx.Response(200, content=synthetic_csv()),
    ]
    with FirmsClient(
        synthetic_key,
        tmp_path,
        transport=httpx.MockTransport(lambda request: responses.pop(0)),
        sleep=synthetic_clock.sleep,
        jitter=lambda low, high: 0,
    ) as client:
        assert client.fetch(START, 5).validation.ok
    assert synthetic_clock.sleeps == [600]


def test_retry_after_date_and_invalid_value() -> None:
    assert retry_after("Thu, 01 Jan 1970 00:17:00 GMT", 1000) == 20
    assert retry_after("not a date", 1000) == 0
    assert retry_after("-5", 1000) == 0


def test_firms_redirect_is_never_followed(tmp_path: Path, synthetic_key: str) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.org/leak"})

    with FirmsClient(synthetic_key, tmp_path, transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(FirmsError, match="http_error"):
            client.fetch(START, 5)
        assert len(calls) == 1


def test_status_and_availability_are_sanitized(tmp_path: Path, synthetic_key: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if "mapkey_status" in request.url.path:
            return httpx.Response(200, json={"note": synthetic_key})
        return httpx.Response(
            200, text=f"data_id,min_date,max_date,note\nVIIRS_SNPP_SP,a,b,{synthetic_key}\n"
        )

    with FirmsClient(synthetic_key, tmp_path, transport=httpx.MockTransport(respond)) as client:
        assert synthetic_key not in json.dumps(client.status())
        assert synthetic_key.encode() not in client.availability()


@pytest.mark.parametrize("body", [b"Invalid API call.", b"data_id,min_date,max_date\nshort\n"])
def test_availability_rejects_invalid_csv(tmp_path: Path, synthetic_key: str, body: bytes) -> None:
    with (
        FirmsClient(
            synthetic_key,
            tmp_path,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
        ) as client,
        pytest.raises(FirmsError),
    ):
        client.availability()


def test_availability_accepts_complete_eof_record(tmp_path: Path, synthetic_key: str) -> None:
    body = b"data_id,min_date,max_date\nVIIRS_SNPP_SP,2012-01-20,2026-10-30"
    with FirmsClient(
        synthetic_key,
        tmp_path,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    ) as client:
        assert client.availability() == body


def test_status_retries_share_gate_and_reserve_quota(
    tmp_path: Path,
    synthetic_key: str,
    synthetic_clock,
) -> None:
    from bolivia_fire.firms.quota import Quota

    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(synthetic_clock.time())
        if len(calls) == 1:
            return httpx.Response(503, text="synthetic outage")
        return httpx.Response(
            200,
            json={
                "transaction_limit": 5000,
                "current_transactions": 0,
                "transaction_interval": "10 minutes",
            },
        )

    quota = Quota(tmp_path, clock=synthetic_clock.time, sleep=synthetic_clock.sleep)
    with FirmsClient(
        synthetic_key,
        tmp_path / "quarantine",
        quota=quota,
        transport=httpx.MockTransport(respond),
        clock=synthetic_clock.time,
        sleep=synthetic_clock.sleep,
        jitter=lambda low, high: 0,
    ) as client:
        assert client.status()["current_transactions"] == 0
    assert calls == [1000, 1060]
    assert json.loads(quota.path.read_text())["events"] == [[1000, 1], [1060, 1]]
