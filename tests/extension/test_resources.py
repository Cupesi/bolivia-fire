import json

import pytest

from bolivia_fire.extension.registration import verify_registration
from bolivia_fire.extension.resources import GIB, Resources


def test_synthetic_resource_projection_refuses_and_checkpoints(synthetic_registration):
    paths, _ = synthetic_registration
    reg = verify_registration(paths)
    with (
        pytest.raises(RuntimeError, match="Projected"),
        Resources(reg, "synthetic_projection") as resources,
    ):
        resources.check(projected_ram=12 * GIB)
    checkpoint = json.loads(
        (
            paths.root / "artifacts/evaluation/extension_resources_synthetic_projection.json"
        ).read_text("utf-8")
    )
    assert checkpoint["status"] == "failed"
    assert checkpoint["peak_process_tree_rss_bytes"] > 0
    assert checkpoint["peak_process_tree_rss_timestamp_utc"] is not None


def test_synthetic_monitoring_failure_refuses(synthetic_registration, monkeypatch):
    paths, _ = synthetic_registration
    monitor = Resources(verify_registration(paths), "synthetic_monitoring")

    def fail_memory():
        raise PermissionError("synthetic process inaccessible")

    monkeypatch.setattr(monitor.process, "memory_info", fail_memory)
    with pytest.raises(RuntimeError, match="monitoring failed"):
        monitor.check()
