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


def test_synthetic_projection_uses_current_rss_after_release(synthetic_registration, monkeypatch):
    from types import SimpleNamespace

    paths, _ = synthetic_registration
    monitor = Resources(verify_registration(paths), "synthetic_release")
    monkeypatch.setattr(monitor.process, "children", lambda **kwargs: [])
    resident = [10 * GIB]
    monkeypatch.setattr(monitor.process, "memory_info", lambda: SimpleNamespace(rss=resident[0]))
    monitor.check()
    resident[0] = GIB
    monitor.check(projected_ram=2 * GIB)
    assert monitor.record("completed")["peak_process_tree_rss_bytes"] == 10 * GIB


def test_synthetic_explicit_memory_override_is_recorded(synthetic_registration):
    paths, _ = synthetic_registration
    reg = verify_registration(paths)
    monitor = Resources(reg, "synthetic_override", memory_limit_gib=20)
    assert reg.contract["resources"]["memory_limit_gib"] == 12
    assert monitor.record("completed")["effective_memory_limit_gib"] == 20
    assert monitor.record("completed")["registered_memory_limit_gib"] == 12


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), 0, 0.25, 21])
def test_synthetic_invalid_memory_override_refuses(synthetic_registration, limit):
    paths, _ = synthetic_registration
    with pytest.raises(ValueError, match="memory limit"):
        Resources(verify_registration(paths), "synthetic_invalid_override", memory_limit_gib=limit)
