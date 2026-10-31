from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import httpx
import numpy as np
import rasterio
from affine import Affine
from numpy.typing import ArrayLike, NDArray
from rasterio.windows import Window, from_bounds

from bolivia_fire.covariates.storage import Budget, append_jsonl

BBOX = (-70.0, -23.0, -57.0, -9.0)


class RasterAccessError(OSError):
    def __init__(self, message: str, record: dict[str, Any]) -> None:
        super().__init__(f"{record['url']}: HTTP {record.get('last_http_status')}: {message}")
        self.record = record


def pixel_indices(
    transform: Affine, longitude: ArrayLike, latitude: ArrayLike,
) -> tuple[NDArray, NDArray]:
    inverse = ~transform
    longitude, latitude = np.asarray(longitude), np.asarray(latitude)
    cols = inverse.a * longitude + inverse.b * latitude + inverse.c
    rows = inverse.d * longitude + inverse.e * latitude + inverse.f
    return np.floor(np.round(rows, 9)).astype(int), np.floor(np.round(cols, 9)).astype(int)


def grid_alignment(transform: Affine) -> dict[str, Any]:
    lon_offset = (transform.c + 180) % 0.05
    lat_offset = (transform.f + 90) % 0.05
    if math.isclose(lon_offset, 0.05, abs_tol=1e-8):
        lon_offset = 0.0
    if math.isclose(lat_offset, 0.05, abs_tol=1e-8):
        lat_offset = 0.0
    return {
        "aligned": bool(
            abs(lon_offset) < 1e-8 and abs(lat_offset) < 1e-8
            and math.isclose(transform.a, 0.05, rel_tol=0, abs_tol=1e-12)
            and math.isclose(transform.e, -0.05, rel_tol=0, abs_tol=1e-12)
            and transform.b == 0 and transform.d == 0
        ),
        "longitude_offset_deg": float(lon_offset),
        "latitude_offset_deg": float(lat_offset),
        "pixel_width_error_deg": float(transform.a - 0.05),
        "pixel_height_error_deg": float(transform.e + 0.05),
    }


def rainfall_nodata(values: NDArray, nodata: float | None) -> NDArray:
    result = np.asarray(values, dtype=np.float32).copy()
    missing = (result == -9999) | ~np.isfinite(result)
    if nodata is not None:
        missing |= result == nodata
    result[missing] = np.nan
    if np.any(result < 0):
        raise ValueError("Rainfall contains an undocumented negative value")
    return result


@contextmanager
def counted_vsicurl(url: str, budget: Budget) -> Iterator[tuple[str, dict[str, Any]]]:
    """Forward only bounded HTTP ranges; account upstream payload bytes durably."""
    metadata: dict[str, Any] = {"url": url, "http_size": None, "bytes_transferred": 0}
    client = httpx.Client(timeout=60, follow_redirects=True)
    header_cache: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_HEAD(self) -> None:
            self.forward(head=True)

        def do_GET(self) -> None:
            self.forward(head=False)

        def forward(self, *, head: bool) -> None:
            if self.path.split("?")[0] != "/raster.tif":
                self.send_error(404)
                return
            requested = self.headers.get("Range")
            if head:
                requested = "bytes=0-16383"
            if not head and not requested:
                self.send_error(400, "A byte range is required")
                return
            headers = {"Accept-Encoding": "identity", "Range": requested}
            try:
                if not head and header_cache and requested:
                    first, last = map(int, requested.removeprefix("bytes=").split("-"))
                    if first >= 0 and last < len(header_cache["body"]):
                        self.send_response(206)
                        self.send_header("Content-Length", str(last-first+1))
                        self.send_header("Content-Range", f"bytes {first}-{last}/"
                                         f"{header_cache['total']}")
                        self.end_headers()
                        self.wfile.write(header_cache["body"][first:last+1])
                        return
                budget.pace_request()
                with client.stream("GET", url, headers=headers) as response:
                    metadata["last_http_status"] = response.status_code
                    metadata["retry_after"] = response.headers.get("retry-after")
                    length = int(response.headers.get("content-length", 0))
                    content_range = response.headers.get("content-range")
                    metadata["http_status"] = response.status_code
                    if response.status_code != 206:
                        metadata.setdefault("request_failures", []).append(response.status_code)
                        append_jsonl(budget.root / "http_requests.jsonl", {
                            "url": url, "range": requested, "status": response.status_code,
                            "bytes": 0, "retry_after": response.headers.get("retry-after"),
                        })
                        status = response.status_code if response.status_code >= 400 else 502
                        self.send_error(status, "Upstream raster request failed")
                        return
                    if length <= 0 or length > 8 * 1024**2 or not content_range:
                        raise RuntimeError("Unbounded or invalid HTTP range response")
                    chunks = bytearray()
                    try:
                        for chunk in response.iter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > length:
                                raise RuntimeError("HTTP range exceeded its declared length")
                    finally:
                        budget.consume(len(chunks))
                        metadata["bytes_transferred"] += len(chunks)
                    body = bytes(chunks)
                    if len(body) != length:
                        raise RuntimeError("Incomplete HTTP range response")
                    total = int(content_range.rsplit("/", 1)[1])
                    metadata["http_size"] = total
                    if content_range.startswith("bytes 0-"):
                        header_cache.update(body=body, total=total)
                    append_jsonl(budget.root / "http_requests.jsonl", {
                        "url": url, "range": requested, "bytes": len(body),
                        "status": response.status_code,
                    })
                    self.send_response(200 if head else 206)
                    if head:
                        self.send_header("Content-Length", str(total))
                        self.end_headers()
                        return
                    names = ("Content-Length", "Content-Range", "Accept-Ranges", "Content-Type")
                    for name in names:
                        if name in response.headers:
                            self.send_header(name, response.headers[name])
                    self.end_headers()
                    if body:
                        self.wfile.write(body)
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                metadata["error"] = str(exc)
                self.send_error(502, "Raster range read failed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"/vsicurl/http://127.0.0.1:{server.server_port}/raster.tif", metadata
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        client.close()


def raster_metadata(src: rasterio.DatasetReader) -> dict[str, Any]:
    tiled = all(shape[0] > 1 and shape[1] < src.width for shape in src.block_shapes)
    return {
        "width": src.width, "height": src.height, "crs": str(src.crs),
        "transform": list(src.transform)[:6], "block_shapes": list(src.block_shapes),
        "overviews": src.overviews(1), "tiled": tiled,
        "layout": src.tags(ns="IMAGE_STRUCTURE").get("LAYOUT"), "nodata": src.nodata,
        "alignment": grid_alignment(src.transform),
    }


def read_rainfall_window(url: str, budget: Budget) -> tuple[NDArray, dict[str, Any]]:
    with counted_vsicurl(url, budget) as (vsi_path, record):
        try:
            return _read_window(vsi_path, record)
        except OSError as exc:
            raise RasterAccessError(str(exc), record) from None


def _read_window(vsi_path: str, record: dict[str, Any]) -> tuple[NDArray, dict[str, Any]]:
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO",
        GDAL_HTTP_TIMEOUT="60", GDAL_CACHEMAX=32 * 1024**2,
        GDAL_HTTP_MAX_RETRY="0", CPL_VSIL_CURL_USE_HEAD="YES",
        CPL_VSIL_CURL_CHUNK_SIZE="16384",
        GDAL_HTTP_MULTIRANGE="SERIAL", GDAL_NUM_THREADS="1",
    ), rasterio.open(vsi_path) as src:
        record.update(raster_metadata(src))
        if not record["tiled"] or not record["overviews"] or record["layout"] != "COG":
            raise RuntimeError("CHIRPS is not a tiled COG with overviews; extraction stopped")
        if src.crs != rasterio.crs.CRS.from_epsg(4326):
            raise ValueError("CHIRPS must use EPSG:4326")
        native = from_bounds(*BBOX, src.transform)
        col, row = math.floor(native.col_off + 1e-8), math.floor(native.row_off + 1e-8)
        width = math.ceil(native.col_off + native.width - 1e-8) - col
        height = math.ceil(native.row_off + native.height - 1e-8) - row
        window = Window(col, row, width, height)
        values = rainfall_nodata(src.read(1, window=window), src.nodata)
        record["transform"] = list(src.window_transform(window))[:6]
        record["nodata_count"] = int(np.isnan(values).sum())
        return values, record
