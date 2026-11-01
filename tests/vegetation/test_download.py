from __future__ import annotations

import hashlib

import httpx
import pytest

from bolivia_fire.vegetation.client import AppEEARSClient
from bolivia_fire.vegetation.download import (
    Budget,
    ChecksumError,
    IncompleteBundleError,
    download_file,
    validate_bundle,
)

SYNTHETIC_BODY = b"synthetic-raster-bytes"


def synthetic_file():
    return {
        "file_id": "synthetic-file",
        "file_name": "synthetic.tif",
        "file_size": len(SYNTHETIC_BODY),
        "sha256": hashlib.sha256(SYNTHETIC_BODY).hexdigest(),
        "file_type": "tif",
    }


def synthetic_client(handler):
    def transport(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(
                200, json={"token": "synthetic-token", "expiration": "2099-01-01T00:00:00Z"}
            )
        return handler(request)

    return AppEEARSClient(
        "synthetic-user",
        "synthetic-pass",
        transport=httpx.MockTransport(transport),
        sleep=lambda _: None,
    )


def test_checksum_mismatch_never_promotes(tmp_path):
    with (
        synthetic_client(lambda _: httpx.Response(200, content=b"x" * len(SYNTHETIC_BODY))) as c,
        pytest.raises(ChecksumError),
    ):
        download_file(c, "synthetic-task", synthetic_file(), tmp_path, Budget(tmp_path, 0))
    assert not (tmp_path / "synthetic.tif").exists()


def test_interrupted_download_resumes_with_verified_range(tmp_path):
    (tmp_path / "synthetic.tif.part").write_bytes(SYNTHETIC_BODY[:5])

    def handler(request):
        assert request.headers["Range"] == "bytes=5-"
        return httpx.Response(
            206,
            content=SYNTHETIC_BODY[5:],
            headers={
                "Content-Range": f"bytes 5-{len(SYNTHETIC_BODY) - 1}/{len(SYNTHETIC_BODY)}",
            },
        )

    with synthetic_client(handler) as c:
        result = download_file(c, "synthetic-task", synthetic_file(), tmp_path, Budget(tmp_path, 0))
    assert (tmp_path / "synthetic.tif").read_bytes() == SYNTHETIC_BODY
    assert result["bytes_transferred"] == len(SYNTHETIC_BODY) - 5
    assert not (tmp_path / "synthetic.tif.part").exists()


def test_ignored_range_restarts_instead_of_appending(tmp_path):
    (tmp_path / "synthetic.tif.part").write_bytes(SYNTHETIC_BODY[:5])
    with synthetic_client(lambda _: httpx.Response(200, content=SYNTHETIC_BODY)) as c:
        download_file(c, "synthetic-task", synthetic_file(), tmp_path, Budget(tmp_path, 0))
    assert (tmp_path / "synthetic.tif").read_bytes() == SYNTHETIC_BODY


@pytest.mark.parametrize("files", [[], [{"file_id": "bad"}]])
def test_incomplete_bundle_is_rejected(files):
    with pytest.raises(IncompleteBundleError):
        validate_bundle({"task_id": "synthetic-task", "files": files}, ["synthetic-layer"])


def test_bundle_missing_requested_layer_is_rejected():
    with pytest.raises(IncompleteBundleError):
        validate_bundle({"files": [synthetic_file()]}, ["NDVI", "EVI"])


def test_disk_floor_blocks_before_download(tmp_path):
    with pytest.raises(OSError, match="free-space"):
        Budget(tmp_path, minimum_free_gb=1e12).check(1)


def test_interrupted_stream_preserves_partial_and_recovers(tmp_path):
    from bolivia_fire.vegetation.client import TransientError

    synthetic_body = b"a" * (1024 * 1024) + b"synthetic-tail"
    item = {
        **synthetic_file(),
        "file_size": len(synthetic_body),
        "sha256": hashlib.sha256(synthetic_body).hexdigest(),
    }

    class SyntheticInterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield synthetic_body[: 1024 * 1024]
            raise httpx.ReadError("synthetic-disconnection")

    def first(request):
        return httpx.Response(200, stream=SyntheticInterruptedStream())

    with synthetic_client(first) as client:
        client.retries = 0
        with pytest.raises(TransientError):
            download_file(client, "synthetic-task", item, tmp_path, Budget(tmp_path, 0))
    assert (tmp_path / "synthetic.tif.part").stat().st_size == 1024 * 1024
    assert not (tmp_path / "synthetic.tif").exists()

    def second(request):
        assert request.headers["Range"] == "bytes=1048576-"
        return httpx.Response(
            206,
            content=b"synthetic-tail",
            headers={
                "Content-Range": f"bytes 1048576-{len(synthetic_body) - 1}/{len(synthetic_body)}",
            },
        )

    with synthetic_client(second) as client:
        download_file(client, "synthetic-task", item, tmp_path, Budget(tmp_path, 0))
    assert (tmp_path / "synthetic.tif").read_bytes() == synthetic_body


def test_invalid_content_range_never_appends(tmp_path):
    partial = tmp_path / "synthetic.tif.part"
    partial.write_bytes(SYNTHETIC_BODY[:5])
    with (
        synthetic_client(
            lambda _: httpx.Response(
                206, content=SYNTHETIC_BODY, headers={"Content-Range": "bytes 0-20/21"}
            )
        ) as client,
        pytest.raises(IncompleteBundleError),
    ):
        download_file(client, "synthetic-task", synthetic_file(), tmp_path, Budget(tmp_path, 0))
    assert partial.read_bytes() == SYNTHETIC_BODY[:5]


def test_bundle_missing_composite_in_every_layer_is_rejected():
    item = {**synthetic_file(), "file_name": "synthetic_NDVI_doy2024225.tif"}
    with pytest.raises(IncompleteBundleError):
        validate_bundle({"files": [item]}, ["NDVI"], expected_dates={"2024225", "2024241"})
