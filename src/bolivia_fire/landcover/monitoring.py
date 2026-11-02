from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from bolivia_fire.landcover.usage import UsagePaused

METRIC = "earthengine.googleapis.com/project/cpu/in_progress_usage_time"


@dataclass(frozen=True)
class UsageSnapshot:
    total_seconds: float
    latest_point_end: datetime
    observed_at: datetime
    points: int = 0
    series: int = 0


class MonitoringClient:
    def __init__(self, project: str, interval_start: str) -> None:
        self.project = project
        self.interval_start = interval_start

    def read(self) -> UsageSnapshot:
        executable = shutil.which("gcloud")
        if not executable:
            raise UsagePaused("gcloud executable is unavailable")
        try:
            result = subprocess.run(
                [executable, "auth", "print-access-token", "--quiet"],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if result.returncode or not result.stdout.strip():
                raise UsagePaused("gcloud access-token retrieval failed; output suppressed")
            token = result.stdout.strip()
            del result
            parameters = {
                "filter": f'metric.type="{METRIC}"',
                "interval.startTime": self.interval_start,
                "interval.endTime": datetime.now(UTC).isoformat(),
                "view": "FULL", "pageSize": "100000",
            }
            series = []
            try:
                while True:
                    request = urllib.request.Request(
                        f"https://monitoring.googleapis.com/v3/projects/{self.project}/timeSeries?"
                        + urllib.parse.urlencode(parameters),
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    with urllib.request.urlopen(request, timeout=60) as response:
                        payload = json.load(response)
                    series.extend(payload.get("timeSeries", []))
                    if not payload.get("nextPageToken"):
                        break
                    parameters["pageToken"] = payload["nextPageToken"]
            finally:
                del token
            if not series or any(item.get("metricKind") != "DELTA" for item in series):
                raise UsagePaused("Monitoring did not return valid DELTA usage series")
            points = [point for item in series for point in item.get("points", [])]
            values = [float(point["value"]["doubleValue"]) for point in points]
            if not values or any(not math.isfinite(value) or value < 0 for value in values):
                raise UsagePaused("Monitoring usage points are unavailable or invalid")
            return UsageSnapshot(
                math.fsum(values),
                max(datetime.fromisoformat(p["interval"]["endTime"]) for p in points),
                datetime.now(UTC), len(points), len(series),
            )
        except UsagePaused:
            raise
        except Exception:
            # HTTP and process exceptions may contain project identifiers or credential output.
            raise UsagePaused("Cloud Monitoring usage read failed; details suppressed") from None


def wait_for_settlement(
    read: Callable[[], UsageSnapshot],
    *,
    baseline_seconds: float,
    completed_at: datetime,
    require_increase: bool = True,
    latency_seconds: float = 300,
    stable_seconds: float = 120,
    timeout_seconds: float = 900,
    poll_seconds: float = 30,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> UsageSnapshot:
    started = monotonic()
    stable_since = started
    previous = None
    while True:
        try:
            snapshot = read()
        except Exception:
            raise UsagePaused("Cloud Monitoring cannot be read; submissions paused") from None
        if (
            not math.isfinite(snapshot.total_seconds)
            or snapshot.total_seconds < baseline_seconds - 1e-6
            or (previous is not None and snapshot.total_seconds < previous - 1e-6)
        ):
            raise UsagePaused("Cloud Monitoring cumulative usage decreased or is invalid")
        if previous is None or abs(snapshot.total_seconds - previous) > 1e-6:
            stable_since = monotonic()
        previous = snapshot.total_seconds
        if (
            (now() - completed_at).total_seconds() >= latency_seconds
            and monotonic() - stable_since >= stable_seconds
            and (not require_increase or snapshot.total_seconds > baseline_seconds)
            and snapshot.latest_point_end >= completed_at
        ):
            return snapshot
        if monotonic() - started >= timeout_seconds:
            raise UsagePaused("Cloud Monitoring did not settle within the bounded wait")
        sleep(min(poll_seconds, timeout_seconds - (monotonic() - started)))
