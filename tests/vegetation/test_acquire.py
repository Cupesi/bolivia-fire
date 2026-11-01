from __future__ import annotations

import httpx
import pytest

from bolivia_fire.vegetation.acquire import estimate_full, inspect_raster
from bolivia_fire.vegetation.client import AppEEARSClient, TransientError
from bolivia_fire.vegetation.manifest import TaskManifest, UncertainSubmissionError


def test_queue_fills_four_requests_before_waiting_and_refills_after_completion():
    from bolivia_fire.vegetation.acquire import queued_requests

    class SyntheticClient:
        task_queue_limit = 4

    class SyntheticManifest:
        def __init__(self):
            self.calls = []

        def ensure_task(self, client, payload):
            self.calls.append(payload)
            return str(payload["synthetic_year"])

    synthetic_manifest = SyntheticManifest()
    synthetic_payloads = [{"synthetic_year": year} for year in range(2016, 2027)]
    synthetic_client = SyntheticClient()
    queued = queued_requests(synthetic_client, synthetic_manifest, synthetic_payloads)
    assert next(queued) == (synthetic_payloads[0], "2016")
    assert synthetic_manifest.calls == synthetic_payloads[:4]
    assert next(queued) == (synthetic_payloads[1], "2017")
    assert synthetic_manifest.calls == synthetic_payloads[:5]
    synthetic_client.task_queue_limit = 2
    assert next(queued) == (synthetic_payloads[2], "2018")
    assert synthetic_manifest.calls == synthetic_payloads[:5]
    assert len(list(queued)) == 8
    assert synthetic_manifest.calls == synthetic_payloads


def test_nested_bundle_directories_exist_before_download_workers_start(tmp_path, monkeypatch):
    import hashlib
    import importlib

    from bolivia_fire.vegetation.download import Budget

    module = importlib.import_module("bolivia_fire.vegetation.acquire")
    synthetic_item = {
        "file_id": "synthetic-file",
        "file_name": "synthetic-bundle/synthetic_NDVI_doy2025001.tif",
        "file_size": 1,
        "sha256": hashlib.sha256(b"x").hexdigest(),
    }

    class SyntheticClient:
        def wait(self, task_id, timeout):
            return {"status": "done"}

        def bundle(self, task_id):
            return {"task_id": task_id, "files": [synthetic_item]}

    def download(client, task_id, item, raw, budget):
        assert (raw / item["file_name"]).parent.is_dir()
        return {**item, "bytes_transferred": 1}

    monkeypatch.setattr(module, "download_file", download)
    result = module._run_task(
        SyntheticClient(), tmp_path,
        {"params": {"dates": [{"startDate": "01-01-2025", "endDate": "01-01-2025"}]}},
        {"ndvi": "NDVI"}, Budget(tmp_path, 0),
        workers=2, timeout=21600, task_id="synthetic-task",
    )
    assert result["bytes_stored"] == 1


def test_restart_never_submits_duplicate_task(tmp_path):
    submissions = []
    payload = {"task_type": "area", "params": {"synthetic": True}}

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(
                200, json={"token": "synthetic-token", "expiration": "2099-01-01T00:00:00Z"}
            )
        if request.method == "GET":
            return httpx.Response(200, json=[])
        submissions.append(1)
        return httpx.Response(202, json={"task_id": "synthetic-task", "status": "pending"})

    with AppEEARSClient(
        "synthetic-user", "synthetic-pass", transport=httpx.MockTransport(handler)
    ) as c:
        path = tmp_path / "tasks.jsonl"
        assert TaskManifest(path).ensure_task(c, payload) == "synthetic-task"
        assert TaskManifest(path).ensure_task(c, payload) == "synthetic-task"
    assert len(submissions) == 1
    assert "synthetic-token" not in path.read_text()
    assert "synthetic-pass" not in path.read_text()


def test_uncertain_submission_is_not_automatically_resubmitted(tmp_path):
    calls = []

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(
                200, json={"token": "synthetic-token", "expiration": "2099-01-01T00:00:00Z"}
            )
        if request.method == "GET":
            return httpx.Response(200, json=[])
        calls.append(1)
        raise httpx.ReadError("synthetic disconnected", request=request)

    with AppEEARSClient(
        "synthetic-user", "synthetic-pass", transport=httpx.MockTransport(handler), retries=0
    ) as c:
        path = tmp_path / "tasks.jsonl"
        payload = {"task_type": "area", "params": {"synthetic": True}}
        with pytest.raises(TransientError):
            TaskManifest(path).ensure_task(c, payload)
        with pytest.raises(UncertainSubmissionError):
            TaskManifest(path).ensure_task(c, payload)
    assert len(calls) == 1


def test_estimate_uses_actual_composites_and_safety_margin():
    synthetic_pilot = {"composite_bytes": {"2024225": 100, "2024241": 120}}
    result = estimate_full(synthetic_pilot)
    assert result["estimated_bytes_per_composite"] == 110
    # Two in December 2011, 14 complete years of 23, and 14 through July 2026.
    assert result["composite_count"] == 338
    assert result["estimated_transfer_bytes"] == 37180
    assert result["reserved_storage_bytes"] == 55770


def test_estimate_includes_sidecar_files_for_each_year_partition():
    synthetic_pilot = {"composite_bytes": {"2024225": 100}, "bytes_stored": 110}
    result = estimate_full(synthetic_pilot)
    assert result["estimated_transfer_bytes"] == 33960
    assert result["reserved_storage_bytes"] == 50940


def test_inspection_distinguishes_delivered_scale_from_source_scale(tmp_path):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "synthetic_scaled.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        nodata=-3000,
        transform=from_origin(-65, -15, 0.01, 0.01),
    ) as output:
        output.write(np.array([[0.2, 0.6], [-3000, 0.8]], dtype="float32"), 1)
    result = inspect_raster(
        path, "ndvi", {"ScaleFactor": 0.0001, "AddOffset": 0, "FillValue": -3000}
    )
    assert result["delivered_scale"] == 1.0
    assert result["source_scale_factor"] == 0.0001
    assert result["values_already_scaled"] is True
    assert result["nodata"] == -3000
    assert result["observed_max"] == pytest.approx(0.8)


def test_inspection_retains_integer_storage_and_gdal_scale(tmp_path):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "synthetic_native.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=1,
        count=1,
        dtype="int16",
        nodata=-3000,
        crs="EPSG:4326",
        transform=from_origin(-65, -15, 0.01, 0.01),
    ) as output:
        output.scales = (0.0001,)
        output.write(np.array([[2000, -3000]], dtype="int16"), 1)
    result = inspect_raster(path, "ndvi", {"ScaleFactor": 0.0001})
    assert result["values_already_scaled"] is False
    assert result["observed_min"] == 2000
    assert result["delivered_scale"] == 0.0001
