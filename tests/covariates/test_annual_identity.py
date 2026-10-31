import hashlib
import json
from datetime import date

import numpy as np
import pytest

from bolivia_fire.covariates.annual import completed_annual
from bolivia_fire.covariates.annual_transport import AnnualSource, prepare_download
from bolivia_fire.covariates.storage import Budget

TIMESTAMP = "Sat, 31 Oct 2026 00:00:00 GMT"
OLD_TIMESTAMP = "Fri, 30 Oct 2026 00:00:00 GMT"


@pytest.fixture(params=[
    pytest.param(('W/"v1"', 'W/"v1"', None, None, 100, False), id="weak-alone"),
    pytest.param(('W/"v1"', 'W/"v1"', None, TIMESTAMP, 100, False),
                 id="weak-missing-recorded-timestamp"),
    pytest.param(('W/"v1"', 'W/"v1"', TIMESTAMP, TIMESTAMP, 100, True),
                 id="weak-matching-timestamp"),
    pytest.param(('W/"v1"', 'W/"v1"', OLD_TIMESTAMP, TIMESTAMP, 100, False),
                 id="weak-changed-timestamp"),
    pytest.param(('W/"v1"', 'W/"v2"', TIMESTAMP, TIMESTAMP, 100, True),
                 id="weak-changed-etag-matching-timestamp"),
    pytest.param(('W/"v1"', 'W/"v1"', TIMESTAMP, TIMESTAMP, 101, False),
                 id="weak-changed-size"),
    pytest.param(('"v1"', '"v1"', None, None, 100, True), id="strong-alone"),
    pytest.param(('"v1"', '"v2"', TIMESTAMP, TIMESTAMP, 100, False),
                 id="strong-changed-etag"),
    pytest.param(('"v1"', '"v1"', TIMESTAMP, OLD_TIMESTAMP, 100, True),
                 id="strong-matching-etag-changed-timestamp"),
    pytest.param(('"v1"', '"v1"', None, None, 101, False), id="strong-changed-size"),
    pytest.param((None, None, TIMESTAMP, TIMESTAMP, 100, True), id="timestamp-alone"),
    pytest.param((None, None, None, None, 100, False), id="no-validator"),
    pytest.param((None, None, OLD_TIMESTAMP, TIMESTAMP, 100, False),
                 id="changed-timestamp-alone"),
])
def synthetic_identity(request):
    saved_etag, current_etag, saved_modified, current_modified, size, matches = request.param
    source = AnnualSource("https://synthetic.invalid/annual.nc", size, current_etag,
                          current_modified)
    saved = {"url": source.url, "bytes": 100, "etag": saved_etag,
             "last_modified": saved_modified,
             "sha256": hashlib.sha256(b"synthetic annual source").hexdigest()}
    return source, saved, matches


def test_synthetic_completed_annual_requires_source_identity(tmp_path, synthetic_identity):
    source, saved, matches = synthetic_identity
    directory = tmp_path / "chirps_daily/year=2024"
    directory.mkdir(parents=True)
    output = directory / "part.npz"
    np.savez_compressed(output, dates=["2024-01-01"], rain=np.ones((1, 1, 1)))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    complete = {"start": "2024-01-01", "end": "2024-01-01", "sha256": digest}
    (directory / "complete.json").write_text(json.dumps(complete), encoding="utf-8")
    record = {"year": 2024, "sha256": digest, "annual_source": saved}
    (tmp_path / "chirps_annual_manifest.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8",
    )

    result = completed_annual(2024, tmp_path, source, date(2024, 1, 1), date(2024, 1, 1))

    assert result == (record if matches else None)


def test_synthetic_resume_requires_source_identity(tmp_path, synthetic_identity):
    source, saved, matches = synthetic_identity
    path = tmp_path / "annual.nc.part"
    path.write_bytes(b"synthetic")
    path.with_suffix(".part.json").write_text(json.dumps(saved), encoding="utf-8")

    if saved["bytes"] != source.size:
        with pytest.raises(RuntimeError, match="identity changed"):
            prepare_download(source, path, Budget(tmp_path))
        assert path.read_bytes() == b"synthetic"
        return

    _, offset, validator = prepare_download(source, path, Budget(tmp_path))

    assert offset == (9 if matches else 0)
    assert path.read_bytes() == (b"synthetic" if matches else b"")
    if matches:
        assert validator == (source.last_modified if source.etag is None
                             or source.etag.startswith("W/") else source.etag)
