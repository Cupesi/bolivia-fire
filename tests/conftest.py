from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from bolivia_fire.firms.validation import HEADER


@pytest.fixture(autouse=True)
def block_network_by_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.get_closest_marker("live"):
        return

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network is disabled; use httpx.MockTransport or mark the test live")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


@pytest.fixture
def synthetic_key() -> str:
    return "synthetic-firms-key-do-not-use"


@pytest.fixture
def synthetic_csv() -> Callable[..., bytes]:
    def build(**changes: str) -> bytes:
        row = dict(
            zip(
                HEADER,
                (
                    "-17.5",
                    "-63.2",
                    "335.1",
                    "0.4",
                    "0.5",
                    "2024-09-01",
                    "0345",
                    "N",
                    "VIIRS",
                    "n",
                    "2.0",
                    "290.1",
                    "12.5",
                    "N",
                    "0",
                ),
                strict=True,
            )
        )
        row.update(changes)
        return (",".join(HEADER) + "\n" + ",".join(row[name] for name in HEADER) + "\n").encode()

    return build


class SyntheticClock:
    def __init__(self, now: float = 1000) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def synthetic_clock() -> SyntheticClock:
    return SyntheticClock()
