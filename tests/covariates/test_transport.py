from http.client import HTTPConnection

import httpx
import numpy as np
import rasterio
from rasterio.shutil import copy as raster_copy
from rasterio.transform import from_origin

from bolivia_fire.covariates.raster import counted_vsicurl, read_rainfall_window
from bolivia_fire.covariates.storage import Budget


def test_synthetic_proxy_requires_range_and_counts_payload(tmp_path, monkeypatch):
    synthetic_bytes = b"abcdefgh"
    received = []

    def synthetic_response(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(synthetic_bytes))})
        received.append(request.headers.get("Range"))
        return httpx.Response(206, content=synthetic_bytes[:4], headers={
            "Content-Length": "4", "Content-Range": "bytes 0-3/8",
        })

    client = httpx.Client(transport=httpx.MockTransport(synthetic_response))
    monkeypatch.setattr("bolivia_fire.covariates.raster.httpx.Client", lambda **kwargs: client)
    budget = Budget(tmp_path, transfer_limit=10)
    with counted_vsicurl("https://synthetic.invalid/rain.cog", budget) as (vsi, meta):
        url = httpx.URL(vsi.removeprefix("/vsicurl/"))
        connection = HTTPConnection(url.host, url.port)
        connection.request("HEAD", url.path)
        assert connection.getresponse().status == 200
        connection.request("GET", url.path)
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        connection.request("GET", url.path, headers={"Range": "bytes=0-3"})
        response = connection.getresponse()
        assert response.status == 206
        assert response.read() == b"abcd"
        connection.close()
    assert received == ["bytes=0-16383"]
    assert budget.transferred == 4
    assert meta["http_size"] == 8
    assert meta["bytes_transferred"] == 4


def test_synthetic_cog_full_resolution_window_without_head(tmp_path, monkeypatch):
    source = tmp_path / "synthetic.tif"
    with rasterio.open(source, "w", driver="GTiff", height=280, width=260, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_origin(-70, -9, 0.05, 0.05)) as sink:
        synthetic_rain = np.full((280, 260), 2, dtype=np.float32)
        synthetic_rain[0, 0] = -9999
        sink.write(synthetic_rain, 1)
    cog = tmp_path / "synthetic.cog.tif"
    raster_copy(source, cog, driver="COG", blocksize=128, compress="DEFLATE")
    body = cog.read_bytes()
    methods = []

    def synthetic_response(request):
        methods.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(body))})
        start, end = request.headers["Range"].removeprefix("bytes=").split("-")
        first, last = int(start), min(int(end), len(body)-1)
        content = body[first:last+1]
        return httpx.Response(206, content=content, headers={
            "Content-Length": str(len(content)),
            "Content-Range": f"bytes {first}-{last}/{len(body)}",
        })

    client = httpx.Client(transport=httpx.MockTransport(synthetic_response))
    monkeypatch.setattr("bolivia_fire.covariates.raster.httpx.Client", lambda **kwargs: client)
    monkeypatch.setattr(Budget, "pace_request", lambda self: None)
    result, record = read_rainfall_window("https://synthetic.invalid/rain.cog", Budget(tmp_path))
    assert result.shape == (280, 260)
    assert np.isnan(result[0, 0])
    assert result[-1, -1] == 2
    assert record["layout"] == "COG"
    assert record["overviews"]
    assert record["http_size"] == len(body)
    assert "HEAD" not in methods
