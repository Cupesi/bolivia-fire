import hashlib
import json
from datetime import date
from types import SimpleNamespace

import httpx
import netCDF4
import numpy as np
import pytest

from bolivia_fire.covariates.chirps import extract_year
from bolivia_fire.covariates.storage import Budget


@pytest.fixture
def synthetic_netcdf(tmp_path):
    path = tmp_path / "synthetic.nc"
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 3)
        dataset.createDimension("latitude", 4)
        dataset.createDimension("longitude", 5)
        latitude = dataset.createVariable("latitude", "f4", ("latitude",))
        longitude = dataset.createVariable("longitude", "f4", ("longitude",))
        latitude[:] = [-9.125, -9.075, -9.025, -8.975]
        longitude[:] = [-70.075, -70.025, -69.975, -69.925, -69.875]
        time = dataset.createVariable("time", "i4", ("time",))
        time.units = "days since 2024-01-01 00:00:00"
        time.calendar = "gregorian"
        time[:] = [58, 59, 60]
        rain = dataset.createVariable(
            "precip", "f4", ("time", "latitude", "longitude"), fill_value=-9999,
        )
        values = np.arange(60, dtype=np.float32).reshape(3, 4, 5)
        values[1, 1, 2] = -9999
        rain[:] = values
    return path


def test_synthetic_netcdf_native_window_nodata_and_leap_day(synthetic_netcdf):
    from bolivia_fire.covariates.annual import AnnualReader

    with AnnualReader(synthetic_netcdf, bbox=(-70.02, -9.09, -69.91, -9.01)) as reader:
        values, record = reader.read(date(2024, 2, 29))
        np.testing.assert_array_equal(values, [[31, 32, 33], [26, np.nan, 28]])
        assert record["nodata_count"] == 1
        assert record["transform"][2] == pytest.approx(-70.05, abs=1e-5)
        assert record["transform"][5] == pytest.approx(-9, abs=1e-5)
        first, _ = reader.read(date(2024, 2, 28))
        assert first[0, 0] == 11
        last, _ = reader.read(date(2024, 3, 1))
        assert last[0, 0] == 51
        with pytest.raises(ValueError, match="missing"):
            reader.read(date(2024, 3, 2))


def test_synthetic_download_resumes_partial_and_checks_length(tmp_path):
    from bolivia_fire.covariates.annual import AnnualSource, download_annual

    payload = b"synthetic annual bytes"
    path = tmp_path / "annual.nc.part"
    path.write_bytes(payload[:9])
    source = AnnualSource("https://synthetic.invalid/annual.nc", len(payload), '"v1"')
    path.with_suffix(".part.json").write_text(json.dumps({
        "url": source.url, "bytes": source.size, "etag": source.etag,
    }))

    def respond(request):
        assert request.headers["range"] == "bytes=9-"
        assert request.headers["if-range"] == '"v1"'
        return httpx.Response(206, content=payload[9:], headers={
            "Content-Range": f"bytes 9-{len(payload)-1}/{len(payload)}",
            "Content-Length": str(len(payload)-9),
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = download_annual(source, path, Budget(tmp_path), client)
    assert path.read_bytes() == payload
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["bytes_transferred"] == len(payload) - 9


def test_synthetic_annual_refuses_insufficient_space_before_request(tmp_path, monkeypatch):
    from bolivia_fire.covariates.annual import AnnualSource, download_annual

    monkeypatch.setattr("bolivia_fire.covariates.storage.shutil.disk_usage",
                        lambda path: SimpleNamespace(free=55_000_000_009))
    source = AnnualSource("https://synthetic.invalid/annual.nc", 10, None)
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("download started without enough free space"),
    )) as client, pytest.raises(RuntimeError, match="free space"):
        download_annual(source, tmp_path / "annual.nc.part", Budget(tmp_path), client)


def test_synthetic_annual_deletes_download_after_success(synthetic_netcdf, tmp_path):
    from bolivia_fire.covariates.annual import extract_annual_file

    root = tmp_path / "output"
    result = extract_annual_file(
        2024, synthetic_netcdf, root, source={"url": "synthetic", "bytes": 100},
        start=date(2024, 2, 28), end=date(2024, 3, 1),
        bbox=(-70.02, -9.09, -69.91, -9.01),
    )
    assert result["days"] == 3
    assert not synthetic_netcdf.exists()
    with np.load(root / "chirps_daily/year=2024/part.npz") as archive:
        assert archive["dates"].tolist() == ["2024-02-28", "2024-02-29", "2024-03-01"]
        assert archive["rain"].shape == (3, 2, 3)
    lines = (root / "chirps_manifest.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 3
    assert all(len(record["window_sha256"]) == 64 for record in records)


def test_synthetic_annual_mismatch_keeps_cog_and_download(synthetic_netcdf, tmp_path):
    from bolivia_fire.covariates.annual import extract_annual_file

    root = tmp_path / "output"

    def synthetic_cog(day, budget):
        return np.zeros((2, 3), dtype=np.float32), {
            "url": "synthetic.cog", "transform": [0.05, 0, -70.05, 0, -0.05, -9],
        }

    existing = extract_year(2024, root, reader=synthetic_cog,
                            start=date(2024, 2, 28), end=date(2024, 2, 28))
    with pytest.raises(RuntimeError, match="consistency"):
        extract_annual_file(2024, synthetic_netcdf, root, source={"url": "synthetic"},
                            start=date(2024, 2, 28), end=date(2024, 3, 1),
                            bbox=(-70.02, -9.09, -69.91, -9.01))
    assert synthetic_netcdf.exists()
    assert json.loads((root / "chirps_daily/year=2024/complete.json").read_text()) == existing


def test_synthetic_completed_annual_reuses_only_verified_source(synthetic_netcdf, tmp_path):
    from bolivia_fire.covariates.annual import (
        AnnualSource,
        completed_annual,
        extract_annual_file,
    )

    root = tmp_path / "output"
    source = AnnualSource("https://synthetic.invalid/annual.nc", 100, '"v1"',
                          "Sat, 31 Oct 2026 00:00:00 GMT")
    extract_annual_file(
        2024, synthetic_netcdf, root,
        source={"url": source.url, "bytes": 100, "etag": source.etag,
                "last_modified": source.last_modified},
        start=date(2024, 2, 28), end=date(2024, 3, 1),
        bbox=(-70.02, -9.09, -69.91, -9.01),
    )
    assert completed_annual(2024, root, source, date(2024, 2, 28), date(2024, 3, 1))
    assert completed_annual(2024, root, source, date(2024, 1, 1), date(2024, 12, 31)) is None
    changed = AnnualSource(source.url, 100, '"v2"', source.last_modified)
    assert completed_annual(2024, root, changed, date(2024, 2, 28), date(2024, 3, 1)) is None
    (root / "chirps_daily/year=2024/part.npz").write_bytes(b"synthetic corrupt output")
    with pytest.raises(RuntimeError, match="checksum"):
        completed_annual(2024, root, source, date(2024, 2, 28), date(2024, 3, 1))


def test_synthetic_download_rejects_incorrect_range_without_appending(tmp_path):
    from bolivia_fire.covariates.annual import AnnualSource, download_annual

    source = AnnualSource("https://synthetic.invalid/annual.nc", 20, '"v1"')
    path = tmp_path / "annual.nc.part"
    path.write_bytes(b"synthetic")
    path.with_suffix(".part.json").write_text(json.dumps({
        "url": source.url, "bytes": source.size, "etag": source.etag,
    }))
    response = httpx.Response(206, content=b"wrong", headers={
        "Content-Range": "bytes 0-4/20", "Content-Length": "5",
    })
    with (
        httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client,
        pytest.raises(RuntimeError, match="Content-Range"),
    ):
        download_annual(source, path, Budget(tmp_path), client)
    assert path.read_bytes() == b"synthetic"


@pytest.mark.parametrize("shift_all", [False, True])
@pytest.mark.parametrize("axis", ["longitude", "latitude"])
def test_synthetic_netcdf_rejects_displaced_coordinate(synthetic_netcdf, shift_all, axis):
    from bolivia_fire.covariates.annual import AnnualReader

    with netCDF4.Dataset(synthetic_netcdf, "a") as dataset:
        coordinate = dataset[axis]
        if shift_all:
            coordinate[:] = coordinate[:] + 0.001
        else:
            coordinate[2] = coordinate[2] + 0.001
    with (
        pytest.raises(ValueError, match="native grid"),
        AnnualReader(synthetic_netcdf, bbox=(-70.02, -9.09, -69.91, -9.01)),
    ):
        pass


def test_synthetic_netcdf_masks_documented_negative_missing_value(synthetic_netcdf):
    from bolivia_fire.covariates.annual import AnnualReader

    with netCDF4.Dataset(synthetic_netcdf, "a") as dataset:
        rain = dataset["precip"]
        rain.missing_value = np.float32(-123)
        rain[1, 2, 1] = -123
    with AnnualReader(synthetic_netcdf, bbox=(-70.02, -9.09, -69.91, -9.01)) as reader:
        values, record = reader.read(date(2024, 2, 29))
        np.testing.assert_array_equal(values, [[np.nan, 32, 33], [26, np.nan, 28]])
        assert record["nodata_count"] == 2


def test_synthetic_netcdf_accepts_provider_float32_coordinate_arithmetic(synthetic_netcdf):
    from bolivia_fire.covariates.annual import AnnualReader

    with netCDF4.Dataset(synthetic_netcdf, "a") as dataset:
        longitude_step = np.float32(359.95) / np.float32(7199)
        latitude_step = np.float32(119.95) / np.float32(2399)
        dataset["longitude"][:] = (np.float32(-179.975)
                                    + np.arange(2198, 2203, dtype=np.float32) * longitude_step)
        dataset["latitude"][:] = (np.float32(-59.975)
                                   + np.arange(1017, 1021, dtype=np.float32) * latitude_step)
    with AnnualReader(synthetic_netcdf, bbox=(-70.02, -9.09, -69.91, -9.01)) as reader:
        values, _ = reader.read(date(2024, 2, 29))
        np.testing.assert_array_equal(values, [[31, 32, 33], [26, np.nan, 28]])


def test_synthetic_download_restarts_without_entity_validator(tmp_path):
    from bolivia_fire.covariates.annual import AnnualSource, download_annual

    payload = b"synthetic complete annual file"
    source = AnnualSource("https://synthetic.invalid/annual.nc", len(payload), None)
    path = tmp_path / "annual.nc.part"
    path.write_bytes(b"old bytes")
    path.with_suffix(".part.json").write_text(json.dumps({
        "url": source.url, "bytes": source.size, "etag": None,
    }))

    def respond(request):
        assert "range" not in request.headers
        assert "if-range" not in request.headers
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        download_annual(source, path, Budget(tmp_path), client)
    assert path.read_bytes() == payload


@pytest.mark.parametrize("matching", [False, True])
def test_synthetic_download_requires_matching_last_modified_when_no_etag(tmp_path, matching):
    from bolivia_fire.covariates.annual import AnnualSource, download_annual

    payload = b"synthetic complete annual file"
    timestamp = "Sat, 31 Oct 2026 00:00:00 GMT"
    source = AnnualSource("https://synthetic.invalid/annual.nc", len(payload), None, timestamp)
    path = tmp_path / "annual.nc.part"
    path.write_bytes(payload[:9])
    path.with_suffix(".part.json").write_text(json.dumps({
        "url": source.url, "bytes": source.size, "etag": None,
        "last_modified": timestamp if matching else "Fri, 30 Oct 2026 00:00:00 GMT",
    }))

    def respond(request):
        if matching:
            assert request.headers["range"] == "bytes=9-"
            assert request.headers["if-range"] == timestamp
            return httpx.Response(206, content=payload[9:], headers={
                "Content-Range": f"bytes 9-{len(payload)-1}/{len(payload)}",
            })
        assert "range" not in request.headers
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        download_annual(source, path, Budget(tmp_path), client)
    assert path.read_bytes() == payload


@pytest.mark.parametrize("https_checkpoint", [False, True])
def test_synthetic_ftp_download_resumes_and_records_checksum(tmp_path, https_checkpoint):
    from bolivia_fire.covariates.annual import AnnualSource, download_ftp

    payload = b"synthetic annual FTP bytes"
    source = AnnualSource("ftp://ftp.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/"
                          "netcdf/byYear/chirps-v3.0.rnl.2026.days_p05.nc", len(payload), None,
                          "Sat, 31 Oct 2026 00:00:00 GMT")
    path = tmp_path / "annual.nc.part"
    path.write_bytes(payload[:9])
    path.with_suffix(".part.json").write_text(json.dumps({
        "url": (source.url.replace("ftp://ftp.chc", "https://data.chc")
                if https_checkpoint else source.url), "bytes": source.size, "etag": None,
        "last_modified": source.last_modified,
    }))

    class SyntheticFTP:
        def retrbinary(self, command, callback, blocksize, rest):
            assert command == "RETR chirps-v3.0.rnl.2026.days_p05.nc"
            assert rest == 9
            callback(payload[9:])
            return "226 Transfer complete"

    result = download_ftp(source, path, Budget(tmp_path), SyntheticFTP())
    assert path.read_bytes() == payload
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["bytes_transferred"] == len(payload) - 9


def test_synthetic_ftp_refuses_download_above_free_space_floor(tmp_path, monkeypatch):
    from bolivia_fire.covariates.annual import AnnualSource, download_ftp

    monkeypatch.setattr("bolivia_fire.covariates.storage.shutil.disk_usage",
                        lambda path: SimpleNamespace(free=55_000_000_009))
    source = AnnualSource("ftp://synthetic.invalid/annual.nc", 10, None,
                          "Sat, 31 Oct 2026 00:00:00 GMT")
    with pytest.raises(RuntimeError, match="free space"):
        download_ftp(source, tmp_path / "annual.nc.part", Budget(tmp_path), None)


def test_synthetic_http_throttle_falls_back_to_ftp_and_reuses_completed_year(tmp_path, monkeypatch):
    from bolivia_fire.covariates.annual import extract_annual_range

    path = tmp_path / "synthetic_full_window.nc"
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 4)
        dataset.createDimension("latitude", 281)
        dataset.createDimension("longitude", 261)
        dataset.createVariable("latitude", "f4", ("latitude",))[:] = (
            -22.975 + np.arange(281) * 0.05)
        dataset.createVariable("longitude", "f4", ("longitude",))[:] = (
            -70.025 + np.arange(261) * 0.05)
        time = dataset.createVariable("time", "i4", ("time",))
        time.units = "days since 2026-01-01 00:00:00"
        time[:] = [0, 1, 2, 3]
        dataset.createVariable("precip", "f4", ("time", "latitude", "longitude"))[:] = 1
    payload = path.read_bytes()
    fetched = []

    class SyntheticFTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self):
            pass

        def cwd(self, path):
            assert path == "/products/CHIRPS/v3.0/daily/final/rnl/netcdf/byYear"

        def nlst(self):
            return ["chirps-v3.0.rnl.2026.days_p05.nc"]

        def voidcmd(self, command):
            assert command == "TYPE I"

        def size(self, name):
            return len(payload)

        def sendcmd(self, command):
            return "213 20261031000000"

        def retrbinary(self, command, callback, blocksize, rest):
            assert rest is None
            fetched.append(command)
            callback(payload)

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    monkeypatch.setattr("bolivia_fire.covariates.annual.httpx.Client", lambda **kwargs: client)
    monkeypatch.setattr("bolivia_fire.covariates.annual.FTP", SyntheticFTP)
    monkeypatch.setattr(Budget, "pace_request", lambda self: None)
    root = tmp_path / "datasets/processed/covariates"
    first = extract_annual_range(2026, 2026, root)
    assert first["years"][0]["days"] == 4
    assert first["years"][0]["annual_source"]["url"].startswith("ftp://")
    assert json.loads((root / "access_state.json").read_text())["http_status"] == 429
    assert not list((tmp_path / "datasets/.tmp").glob("*.part"))
    with np.load(root / "chirps_daily/year=2026/part.npz") as archive:
        assert archive["rain"].shape == (4, 281, 261)
        assert np.all(archive["rain"] == 1)
    second = extract_annual_range(2026, 2026, root, transport="ftp")
    assert second["years"][0]["reused"]
    assert first["years"][0]["sha256"] == second["years"][0]["sha256"]
    assert fetched == ["RETR chirps-v3.0.rnl.2026.days_p05.nc"]
