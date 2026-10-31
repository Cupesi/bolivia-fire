from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


def rss_bytes(pid: int) -> int:
    if os.name != "nt":
        pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak_working_set",
                "working_set",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    memory_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    handle = kernel.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle:
        raise OSError("Unable to inspect benchmark process")
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not memory_info(handle, ctypes.byref(counters), counters.cb):
            raise OSError("Unable to read benchmark working set")
        return int(counters.working_set)
    finally:
        kernel.CloseHandle(handle)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def measure(root: Path, count: int, *, unprepared: bool = False) -> dict[str, Any]:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = os.environ.copy()
    environment["BOLIVIA_FIRE_DATA_ROOT"] = str(root)
    if unprepared:
        environment["BOLIVIA_FIRE_DISABLE_SERVING"] = "1"
    tick = time.perf_counter()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
            "--log-level",
            "info",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    server_pid = process.pid
    try:
        assert process.stdout is not None
        for line in process.stdout:
            match = re.search(r"Started server process \[(\d+)\]", line)
            if match:
                server_pid = int(match[1])
                break
        else:
            raise RuntimeError("Benchmark server did not report its process ID")
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False
        ) as client:
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Benchmark server exited before becoming ready")
                try:
                    response = client.get("/api/v1/health")
                    response.raise_for_status()
                    break
                except httpx.ConnectError:
                    if time.perf_counter() - tick > 60:
                        raise TimeoutError("Benchmark server did not become ready") from None
                    time.sleep(0.05)
            startup = time.perf_counter() - tick
            startup_rss = rss_bytes(server_pid)
            meta = client.get("/api/v1/meta").raise_for_status().json()
            grid = client.get("/api/v1/grid").raise_for_status().json()
            weeks = client.get("/api/v1/weeks").raise_for_status().json()["weeks"]
            dates = [
                weeks[index * (len(weeks) - 1) // max(count - 1, 1)]["issue_date"]
                for index in range(count)
            ]
            cells = [grid["cell_id"][index * (len(grid["cell_id"]) - 1) // 4] for index in range(5)]
            latencies: dict[str, Any] = {}
            for endpoint in ("layer", "series"):
                timings = []
                sizes = []
                for index in range(count):
                    url = (
                        f"/api/v1/layer?issue_date={dates[index]}&kind=observed"
                        if endpoint == "layer"
                        else f"/api/v1/cells/{cells[index % len(cells)]}/series"
                    )
                    started = time.perf_counter()
                    response = client.get(url)
                    response.raise_for_status()
                    response.json()
                    timings.append((time.perf_counter() - started) * 1000)
                    sizes.append(len(response.content))
                latencies[endpoint] = {
                    "requests": count,
                    "p50_ms": percentile(timings, 0.5),
                    "p95_ms": percentile(timings, 0.95),
                    "max_decoded_response_bytes": max(sizes),
                }
            return {
                "measured_at_utc": datetime.now(UTC).isoformat(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "startup_seconds": startup,
                "rss_after_startup_bytes": startup_rss,
                "rss_after_requests_bytes": rss_bytes(server_pid),
                "rss_method": "GetProcessMemoryInfo WorkingSetSize for the uvicorn server PID"
                if os.name == "nt"
                else "/proc server resident pages",
                "serving_index_enabled": not unprepared
                and (root / "artifacts/serving/manifest.json").exists(),
                "cells": len(grid["cell_id"]),
                "weeks": len(weeks),
                "issue_date_range": meta["issue_date_range"],
                "dataset_snapshot_ids": meta["dataset_snapshot_ids"],
                "data_ready": client.get("/api/v1/health").json()["data_ready"],
                "latency": latencies,
                "series_cells": cells,
                "layer_issue_dates": dates,
                "free_disk_bytes": shutil.disk_usage(root).free,
                "workload": "One uvicorn worker; sequential loopback HTTP "
                "with persistent client; "
                "gzip accepted; response downloaded and JSON decoded; no warm-up requests. "
                "Layers: observed, dates evenly spaced over prepared weeks. "
                "Series: complete date range, five evenly spaced grid cells rotated.",
            }
    finally:
        if os.name == "nt" and server_pid != process.pid:
            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x0001, False, server_pid)
            if handle:
                kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                kernel.TerminateProcess(handle, 0)
                kernel.CloseHandle(handle)
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--unprepared", action="store_true")
    arguments = parser.parse_args()
    if not 1 <= arguments.requests <= 1000:
        parser.error("--requests must be between 1 and 1000")
    result = measure(arguments.root.resolve(), arguments.requests, unprepared=arguments.unprepared)
    content = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if arguments.output:
        arguments.output.write_text(content, encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "measured_at_utc",
                    "startup_seconds",
                    "rss_after_startup_bytes",
                    "rss_after_requests_bytes",
                    "serving_index_enabled",
                    "latency",
                    "free_disk_bytes",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
