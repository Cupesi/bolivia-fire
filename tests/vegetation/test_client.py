from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from bolivia_fire.vegetation.client import (
    AppEEARSClient,
    AuthenticationError,
    CircuitOpenError,
    MalformedResponseError,
    TaskError,
)


def synthetic_client(handler, **kwargs):
    return AppEEARSClient(
        "synthetic-user",
        "synthetic-password",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        **kwargs,
    )


def synthetic_login(number=1, expiration="2099-01-01T00:00:00Z"):
    return httpx.Response(
        200,
        json={
            "token": f"synthetic-token-{number}",
            "expiration": expiration,
            "token_type": "Bearer",
        },
    )


@pytest.mark.parametrize("status", [401, 403])
def test_auth_refresh_is_bounded(status):
    logins = []

    def handler(request):
        if request.url.path.endswith("/login"):
            logins.append(1)
            return synthetic_login(len(logins))
        return httpx.Response(status, json={"message": "synthetic-password"})

    with synthetic_client(handler) as client, pytest.raises(AuthenticationError):
        client.task("synthetic-task")
    assert len(logins) == 2


def test_expired_token_is_refreshed_before_next_request():
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    logins = []
    authorizations = []

    def handler(request):
        if request.url.path.endswith("/login"):
            logins.append(1)
            return synthetic_login(len(logins), "2026-01-01T00:02:00Z")
        authorizations.append(request.headers["Authorization"])
        return httpx.Response(200, json={"task_id": "synthetic-task", "status": "done"})

    with synthetic_client(handler, now=lambda: now[0]) as client:
        client.task("synthetic-task")
        now[0] += timedelta(minutes=3)
        client.task("synthetic-task")
    assert authorizations == ["Bearer synthetic-token-1", "Bearer synthetic-token-2"]


def test_401_refresh_recovers_request():
    def handler(request):
        if request.url.path.endswith("/login"):
            handler.logins += 1
            return synthetic_login(handler.logins)
        if request.headers["Authorization"] == "Bearer synthetic-token-1":
            return httpx.Response(401)
        return httpx.Response(200, json={"task_id": "synthetic-task", "status": "done"})

    handler.logins = 0
    with synthetic_client(handler) as client:
        assert client.task("synthetic-task")["status"] == "done"


def test_malformed_json_has_safe_error(caplog):
    def handler(request):
        return httpx.Response(200, text="synthetic-password synthetic-token-1")

    with synthetic_client(handler) as client, pytest.raises(MalformedResponseError) as caught:
        client.products()
    assert "synthetic-password" not in str(caught.value)
    assert "synthetic-token" not in caplog.text


def test_task_error_does_not_echo_response_secrets():
    def handler(request):
        if request.url.path.endswith("/login"):
            return synthetic_login()
        return httpx.Response(
            200,
            json={
                "task_id": "synthetic-task",
                "status": "error",
                "error": "synthetic-password",
            },
        )

    with synthetic_client(handler) as client, pytest.raises(TaskError) as caught:
        client.wait("synthetic-task", timeout=1)
    assert "synthetic-password" not in str(caught.value)


def test_rate_limit_honors_retry_after_and_circuit_breaker():
    waits = []
    with AppEEARSClient(
        "synthetic-user",
        "synthetic-password",
        transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "7"})),
        sleep=waits.append,
        retries=1,
        circuit_threshold=2,
    ) as client:
        with pytest.raises(CircuitOpenError):
            client.products()
        with pytest.raises(CircuitOpenError):
            client.products()
    assert waits == [7.0]


def test_credential_transport_exception_is_suppressed(caplog):
    def handler(request):
        raise httpx.ConnectError("synthetic-password synthetic-user", request=request)

    with synthetic_client(handler, retries=0) as client, pytest.raises(Exception) as caught:
        client.login()
    assert "synthetic-password" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert "synthetic-password" not in caplog.text


def test_wait_uses_five_minute_minimum_with_backoff():
    synthetic_waits = []
    synthetic_statuses = iter(["processing", "processing", "done"])

    def handler(request):
        if request.url.path.endswith("/login"):
            return synthetic_login()
        return httpx.Response(200, json={"status": next(synthetic_statuses)})

    with AppEEARSClient(
        "synthetic-user",
        "synthetic-password",
        transport=httpx.MockTransport(handler),
        sleep=synthetic_waits.append,
    ) as client:
        assert client.wait("synthetic-task")["status"] == "done"
    assert len(synthetic_waits) == 2
    assert 300 <= synthetic_waits[0] < synthetic_waits[1] <= 902


def test_recovered_rate_limit_reduces_remote_queue_limit():
    synthetic_responses = iter([httpx.Response(429), httpx.Response(200, json=[])])
    with synthetic_client(lambda _: next(synthetic_responses)) as client:
        assert client.task_queue_limit == 4
        assert client.products() == []
        assert client.task_queue_limit == 2


def test_default_polling_window_allows_five_hours_of_processing(monkeypatch):
    synthetic_clock = [0.0]
    synthetic_polls = []

    def sleep(seconds):
        synthetic_clock[0] += seconds

    def handler(request):
        if request.url.path.endswith("/login"):
            return synthetic_login()
        synthetic_polls.append(synthetic_clock[0])
        status = "done" if synthetic_clock[0] >= 18000 else "processing"
        return httpx.Response(200, json={"status": status})

    monkeypatch.setattr("bolivia_fire.vegetation.client.time.monotonic", lambda: synthetic_clock[0])
    with AppEEARSClient(
        "synthetic-user", "synthetic-password",
        transport=httpx.MockTransport(handler), sleep=sleep,
    ) as client:
        assert client.wait("synthetic-task")["status"] == "done"
    assert synthetic_polls[-1] >= 18000
    assert all(b - a >= 300 for a, b in zip(synthetic_polls, synthetic_polls[1:], strict=False))
