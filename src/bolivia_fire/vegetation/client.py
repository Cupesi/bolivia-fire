from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

BASE_URL = "https://appeears.earthdatacloud.nasa.gov/api"


class AppEEARSError(RuntimeError):
    pass


class AuthenticationError(AppEEARSError):
    pass


class RateLimitError(AppEEARSError):
    pass


class MalformedResponseError(AppEEARSError):
    pass


class CircuitOpenError(AppEEARSError):
    pass


class TaskError(AppEEARSError):
    pass


class TransientError(AppEEARSError):
    pass


class AppEEARSClient:
    def __init__(
        self,
        username: str,
        password: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        retries: int = 4,
        circuit_threshold: int = 6,
        circuit_cooldown: float = 120,
    ) -> None:
        self._username, self._password = username, password
        self._token = ""
        self._expiration = datetime.min.replace(tzinfo=UTC)
        self._auth_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._rate_limited = threading.Event()
        self._sleep, self._now = sleep, now
        self.retries = retries
        self._failures, self._opened = 0, 0.0
        self._threshold, self._cooldown = circuit_threshold, circuit_cooldown
        self.http = httpx.Client(
            transport=transport,
            follow_redirects=True,
            timeout=httpx.Timeout(connect=30, read=180, write=120, pool=60),
        )
        # HTTP debug logs can expose Basic headers or signed download redirects.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

    @property
    def task_queue_limit(self) -> int:
        return 2 if self._rate_limited.is_set() else 4

    def __enter__(self) -> AppEEARSClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.http.close()
        self._token = ""

    def _failure(self) -> None:
        with self._state_lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened = time.monotonic()
                raise CircuitOpenError("AppEEARS transient-error circuit is open") from None

    def _guard(self) -> None:
        with self._state_lock:
            if self._opened:
                if time.monotonic() - self._opened < self._cooldown:
                    raise CircuitOpenError("AppEEARS transient-error circuit is open")
                self._opened, self._failures = 0.0, 0

    def _delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None and (value := response.headers.get("Retry-After")):
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    return max(0.0, (parsedate_to_datetime(value) - self._now()).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return min(60, 2**attempt) + random.uniform(0, 1)

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        stream: bool = False,
        retry_safe: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        refreshed = False
        attempt = 0
        base_headers = dict(kwargs.pop("headers", {}))
        auth = kwargs.pop("auth", None)
        while True:
            self._guard()
            headers = dict(base_headers)
            if authenticated:
                with self._auth_lock:
                    if not self._token or self._expiration <= self._now():
                        self.login()
                    headers["Authorization"] = f"Bearer {self._token}"
            response = None
            try:
                request = self.http.build_request(
                    method, BASE_URL + path, headers=headers, **kwargs
                )
                response = self.http.send(request, stream=stream, auth=auth)
            except httpx.HTTPError:
                self._failure()
                if not retry_safe or attempt >= self.retries:
                    raise TransientError(
                        "AppEEARS transport failed; response not recorded"
                    ) from None
            else:
                status = response.status_code
                if status in {401, 403}:
                    response.close()
                    if authenticated and not refreshed:
                        with self._auth_lock:
                            self._token = ""
                            self.login()
                        refreshed = True
                        continue
                    if status == 403:
                        self._rate_limited.set()
                    raise AuthenticationError("AppEEARS authentication rejected") from None
                if status == 429 or status >= 500:
                    if status == 429:
                        self._rate_limited.set()
                    delay = self._delay(response, attempt)
                    response.close()
                    self._failure()
                    if not retry_safe or attempt >= self.retries:
                        kind = RateLimitError if status == 429 else TransientError
                        raise kind(f"AppEEARS HTTP {status}; retry budget exhausted") from None
                    self._sleep(delay)
                    attempt += 1
                    continue
                if not 200 <= status < 300:
                    response.close()
                    raise AppEEARSError(f"AppEEARS HTTP {status}") from None
                with self._state_lock:
                    self._failures = 0
                return response
            self._sleep(self._delay(None, attempt))
            attempt += 1

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError:
            raise MalformedResponseError("AppEEARS returned malformed JSON") from None
        finally:
            response.close()

    def login(self) -> None:
        with self._auth_lock:
            data = self._json(
                "POST",
                "/login",
                authenticated=False,
                auth=httpx.BasicAuth(self._username, self._password),
            )
            try:
                token = data["token"]
                expiration = datetime.fromisoformat(data["expiration"].replace("Z", "+00:00"))
                if not isinstance(token, str) or not token or expiration.tzinfo is None:
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                raise MalformedResponseError(
                    "AppEEARS login response lacks a valid token"
                ) from None
            self._token, self._expiration = token, expiration

    def logout(self) -> None:
        with self._auth_lock:
            if self._token:
                try:
                    self._request("POST", "/logout").close()
                finally:
                    self._token = ""

    def products(self) -> list[dict[str, Any]]:
        data = self._json("GET", "/product", authenticated=False)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise MalformedResponseError("AppEEARS product response is not a list")
        return data

    def layers(self, product: str) -> dict[str, Any]:
        data = self._json("GET", f"/product/{product}", authenticated=False)
        if not isinstance(data, dict) or not data:
            raise MalformedResponseError("AppEEARS layer response is not a mapping")
        return data

    def tasks(self) -> list[dict[str, Any]]:
        data = self._json("GET", "/task")
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise MalformedResponseError("AppEEARS task list is malformed")
        return data

    def submit(self, payload: dict[str, Any]) -> str:
        data = self._json("POST", "/task", json=payload, retry_safe=False)
        if not isinstance(data, dict) or not isinstance(data.get("task_id"), str):
            raise MalformedResponseError("AppEEARS submission lacks a task id")
        return data["task_id"]

    def task(self, task_id: str) -> dict[str, Any]:
        data = self._json("GET", f"/task/{task_id}")
        if not isinstance(data, dict) or not isinstance(data.get("status"), str):
            raise MalformedResponseError("AppEEARS task response lacks status")
        return data

    def wait(self, task_id: str, *, timeout: float = 21600) -> dict[str, Any]:
        deadline, delay = time.monotonic() + timeout, 300.0
        while True:
            data = self.task(task_id)
            if data["status"] == "done":
                return data
            if data.get("error") or data["status"] in {"error", "failed", "cancelled"}:
                raise TaskError("AppEEARS task failed; inspect the task in the service")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("AppEEARS task polling timeout; task id is persisted")
            if remaining < 300:
                self._sleep(remaining)
                raise TimeoutError("AppEEARS task polling timeout; task id is persisted")
            self._sleep(min(remaining, delay + random.uniform(0, 2)))
            delay = min(900, delay * 1.5)

    def bundle(self, task_id: str) -> dict[str, Any]:
        data = self._json("GET", f"/bundle/{task_id}")
        if not isinstance(data, dict):
            raise MalformedResponseError("AppEEARS bundle is not a mapping")
        return data

    def stream_file(self, task_id: str, file_id: str, offset: int) -> httpx.Response:
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        return self._request("GET", f"/bundle/{task_id}/{file_id}", stream=True, headers=headers)
