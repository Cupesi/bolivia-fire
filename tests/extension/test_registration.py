import json

import pytest

from bolivia_fire.extension.registration import verify_registration


def test_synthetic_committed_registration_accepts_exact_manifest(synthetic_registration):
    paths, _ = synthetic_registration
    proof = verify_registration(paths)
    assert len(proof.runs) == 10
    assert proof.commit == "a" * 40
    assert proof.runs[0]["availability_lag_days"] == 16
    assert proof.runs[1]["availability_lag_days"] == 32


@pytest.mark.parametrize("failure", ["missing", "uncommitted", "dirty", "hash", "attestation"])
def test_synthetic_registration_refusals(synthetic_registration, failure):
    paths, blobs = synthetic_registration
    name = "artifacts/evaluation/extension_registration.json"
    path = paths.root / name
    if failure == "missing":
        path.unlink()
    elif failure == "uncommitted":
        del blobs[name]
    elif failure == "dirty":
        path.write_bytes(path.read_bytes() + b"\n")
    elif failure == "hash":
        contract = paths.root / "configs/extension_vegetation.yaml"
        contract.write_bytes(contract.read_bytes() + b"\n")
    else:
        entry = json.loads(path.read_text("utf-8"))
        entry["no_extension_labels_or_metrics_accessed"] = False
        path.write_text(json.dumps(entry), encoding="utf-8")
        blobs[name] = path.read_bytes()
    with pytest.raises(ValueError):
        verify_registration(paths)
