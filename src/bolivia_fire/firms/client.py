from __future__ import annotations

import csv
import io
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from bolivia_fire.firms.quota import Quota
from bolivia_fire.firms.validation import (
    BBOX,
    BBox,
    Outcome,
    Validation,
    error_outcome,
    validate_csv,
)
from bolivia_fire.redact import Redactor

BASE = "https://firms.modaps.eosdis.nasa.gov"
MAX_BODY_BYTES = 128 * 1024 * 1024


class FirmsError(RuntimeError):
    def __init__(self, outcome: Outcome, message: str) -> None:
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class Download:
    body: bytes
    validation: Validation
    url: str
    http_status: int
    retrieval_utc: str


def retry_after(value: str | None, now: float) -> float:
    if value is None:
        return 0
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 0


class FirmsClient:
    def __init__(
        self,
        key: str,
        quarantine: Path,
        *,
        quota: Quota | None = None,
        transactions_per_day: int = 2,
        transport: httpx.BaseTransport | None = None,
        retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if retries < 0:
            raise ValueError("Retries must be nonnegative")
        if type(transactions_per_day) is not int or transactions_per_day < 1:
            raise ValueError("Transactions per day must be a positive integer")
        self._key = key
        self.redactor = Redactor([key])
        self.quarantine = quarantine
        self.quota = quota
        self.transactions_per_day = transactions_per_day
        self.retries = retries
        self.sleep = sleep
        self.clock = clock
        self.jitter = jitter
        self.data_requests = 0
        self.http = httpx.Client(
            verify=True,
            follow_redirects=False,
            transport=transport,
            timeout=httpx.Timeout(connect=20, read=120, write=30, pool=20),
        )

    def __enter__(self) -> FirmsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.http.close()

    def area_url(self, start: date, day_range: int, bbox: BBox = BBOX) -> str:
        if not 1 <= day_range <= 5:
            raise ValueError("FIRMS day_range must be between 1 and 5")
        coordinates = ",".join(f"{coordinate:g}" for coordinate in bbox)
        return f"{BASE}/api/area/csv/{self._key}/VIIRS_SNPP_SP/{coordinates}/{day_range}/{start}"

    def _quarantine(self, body: bytes, outcome: Outcome) -> None:
        self.quarantine.mkdir(parents=True, exist_ok=True)
        name = f"firms_{outcome}_{uuid4().hex}.txt"
        (self.quarantine / name).write_bytes(
            self.redactor.text(body.decode("utf-8", errors="replace")).encode("utf-8"),
        )

    def _request(self, url: str, cost: int = 1, *, status_request: bool = False) -> httpx.Response:
        for attempt in range(self.retries + 1):
            reservation = None
            if self.quota is not None and not status_request:
                reservation = self.quota.reserve(cost)
            elif self.quota is not None and attempt > 0:
                reservation = self.quota.reserve(1, status_request=True)
            chunks = bytearray()
            try:
                if "/api/area/" in url:
                    self.data_requests += 1
                # Bound memory even if a server returns an unexpectedly large body.
                with self.http.stream("GET", url, follow_redirects=False) as stream:
                    for chunk in stream.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_BODY_BYTES:
                            self._quarantine(bytes(chunks[:16384]), Outcome.TRUNCATED)
                            raise FirmsError(Outcome.TRUNCATED, "FIRMS response exceeds size limit")
                    length = stream.headers.get("Content-Length")
                    received = stream.num_bytes_downloaded or len(chunks)
                    if length is not None and (
                        not length.isascii() or not length.isdigit() or int(length) != received
                    ):
                        self._quarantine(bytes(chunks), Outcome.TRUNCATED)
                        raise FirmsError(Outcome.TRUNCATED, "FIRMS Content-Length mismatch")
                    # iter_bytes has already decoded any HTTP content encoding.
                    headers = httpx.Headers(stream.headers)
                    headers.pop("content-encoding", None)
                    response = httpx.Response(
                        stream.status_code,
                        headers=headers,
                        content=bytes(chunks),
                        request=stream.request,
                    )
                if reservation is not None and self.quota is not None:
                    self.quota.record_response(reservation)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                if chunks:
                    self._quarantine(bytes(chunks), Outcome.TRUNCATED)
                if attempt == self.retries:
                    outcome = Outcome.TRUNCATED if chunks else Outcome.HTTP_ERROR
                    raise FirmsError(outcome, self.redactor.text(exc)) from None
                self.sleep(self.jitter(0, min(60, 2**attempt)))
                continue
            except httpx.HTTPError as exc:
                if chunks:
                    self._quarantine(bytes(chunks), Outcome.TRUNCATED)
                raise FirmsError(Outcome.HTTP_ERROR, self.redactor.text(exc)) from None
            problem = error_outcome(response.content)
            is_quota = response.status_code == 429 or problem == Outcome.QUOTA_EXCEEDED
            permanent_body = problem == Outcome.INVALID_KEY or (
                b"invalid api call" in response.content[:16384].lower()
            )
            retryable = not permanent_body and (is_quota or 500 <= response.status_code < 600)
            if not retryable:
                return response
            outcome = Outcome.QUOTA_EXCEEDED if is_quota else Outcome.HTTP_ERROR
            self._quarantine(response.content, outcome)
            delay = retry_after(response.headers.get("Retry-After"), self.clock())
            if is_quota and self.quota is not None:
                self.quota.exhausted(delay)
            if attempt == self.retries:
                raise FirmsError(outcome, f"FIRMS {outcome}: {self.redactor.text(url)}")
            self.sleep(max(delay, 600 if is_quota else 0, self.jitter(0, min(60, 2**attempt))))
        raise AssertionError("Retry loop did not return")

    def fetch(self, start: date, day_range: int, bbox: BBox = BBOX) -> Download:
        if self.quota is not None:
            self.quota.reconcile(self.status_uncoordinated)
        url = self.area_url(start, day_range, bbox)
        response = self._request(url, day_range * self.transactions_per_day)
        validation = validate_csv(response.content, start, day_range, bbox, response.status_code)
        if validation.ok and self._key.encode() in response.content:
            validation = Validation(Outcome.SCHEMA_DRIFT)
        if not validation.ok:
            self._quarantine(response.content, validation.outcome)
            raise FirmsError(
                validation.outcome,
                f"FIRMS {validation.outcome}: {self.redactor.text(url)}",
            )
        return Download(
            response.content,
            validation,
            self.redactor.text(url),
            response.status_code,
            datetime.fromtimestamp(self.clock(), UTC).isoformat(),
        )

    def status_uncoordinated(self) -> dict[str, Any]:
        url = f"{BASE}/mapserver/mapkey_status/?MAP_KEY={self._key}"
        response = self._request(url, status_request=True)
        problem = error_outcome(response.content)
        try:
            result = response.json()
        except ValueError:
            result = None
        if response.status_code != 200 or problem or not isinstance(result, dict):
            outcome = problem or Outcome.HTTP_ERROR
            self._quarantine(response.content, outcome)
            raise FirmsError(outcome, f"Invalid FIRMS status response: {self.redactor.text(url)}")
        return self.redactor.sanitize(result)

    def status(self, *, fresh: bool = False) -> dict[str, Any]:
        if self.quota is not None:
            return self.quota.reconcile(self.status_uncoordinated, fresh=fresh)
        return self.status_uncoordinated()

    def availability(self) -> bytes:
        if self.quota is not None:
            self.quota.reconcile(self.status_uncoordinated)
        url = f"{BASE}/api/data_availability/csv/{self._key}/all"
        response = self._request(url)
        problem = error_outcome(response.content)
        try:
            rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), strict=True))
            valid = (
                len(rows) >= 2
                and len(rows[0]) >= 3
                and rows[0][0].lower() in {"data_id", "source", "product"}
                and all(len(row) == len(rows[0]) for row in rows)
            )
        except (ValueError, csv.Error):
            valid = False
        if response.status_code != 200 or problem or not valid:
            outcome = problem or Outcome.SCHEMA_DRIFT
            self._quarantine(response.content, outcome)
            raise FirmsError(outcome, "Invalid FIRMS availability response")
        return self.redactor.text(response.content.decode("utf-8-sig")).encode("utf-8")

    def __repr__(self) -> str:
        return f"FirmsClient(data_requests={self.data_requests})"
