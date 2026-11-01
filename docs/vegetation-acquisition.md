# MOD13A2.061 acquisition

The acquisition uses the [official AppEEARS API](https://appeears.earthdatacloud.nasa.gov/api/)
and discovers product and layer identifiers at runtime. The
[NASA collection record](https://cmr.earthdata.nasa.gov/search/concepts/C2565788905-LPCLOUD.html)
confirms DOI `10.5067/MODIS/MOD13A2.061`; data use is described in
[MODIS.md](../datasets/licenses/MODIS.md).

Set `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD` in the environment or repository
credential file. The loader checks the environment first. Credentials and tokens
remain in memory; HTTP debug logging is disabled. The client logs in with HTTP
Basic authentication, tracks token expiration, refreshes once after an HTTP 401
or 403, and logs out after acquisition.

Run from a local checkout with its existing environment:

```sh
uv run --no-sync bolivia-fire vegetation pilot --workers 2 --timeout 21600
uv run --no-sync bolivia-fire vegetation full --workers 2 --timeout 21600
```

The pilot covers 2024-08-01 through 2024-09-30. Full acquisition covers
2011-12-01 through 2026-07-31 in inclusive calendar-year partitions. The AOI is
the checksum-verified pinned Bolivia ADM0 geometry with a 0.05-degree buffer.
Native GeoTIFF output preserves the source sinusoidal grid and resolution,
avoiding an acquisition-time reprojection. Later spatial aggregation must handle
that CRS explicitly.

`datasets/manifests/appeears_tasks.jsonl` records request parameters and task IDs.
A deterministic request name and a durable pre-submission intent prevent duplicate
POSTs on restart. After a lost submission response the client reconciles the
server task list; if no matching task is visible, it stops for review instead of
submitting again. Do not remove an unresolved intent to force a retry without
checking the service account's task list.

Full acquisition keeps up to four submitted tasks queued, reattaching to saved
task IDs before considering a submission. The documented commands use a six-hour
polling window for each task, with intervals increasing from 300 to 900 seconds
plus jitter. The queue shrinks to two after an HTTP 429 or a persistent HTTP 403; already queued
tasks drain without cancellation. The current API reference states no concurrent
task limit. Local acquisition uses APIs and CLI commands only.

The documented commands use two download workers, `.part` files, HTTP Range when supported,
validated Content-Range headers, bundle byte counts and SHA-256 digests. A server
that ignores Range causes a fresh overwrite of the partial file. Verified files
are atomically promoted. Validated bundle directories are created before the
download pool starts, avoiding a Windows path-prefix race when a missing parent
appears during concurrent path resolution. File containment checks remain active.
Existing final files are checked before reuse.
Transient requests use bounded exponential backoff with jitter and Retry-After;
repeated failures open a temporary circuit breaker. Task submission is not
automatically retried after a transport or server failure because its outcome
could be ambiguous.

The pilot report records delivered CRS, pixel dimensions, scale, offset, nodata,
sample raster ranges, per-composite bytes and runtime. The full estimate uses the
pilot's mean bytes per composite across 338 requested composites and reserves
1.5 times that estimate. These estimates do not guarantee future bundle sizes.
The full run requires a completed pilot and a successful initial storage check;
every bundle and streamed write also checks the 55 GiB free-space floor.

Sanitized discovery, bundle, download, pilot and full reports live under
`datasets/manifests/`. Raw bundles live under `datasets/raw/vegetation/{task_id}/`.
Reports contain service file checksums and retrieval times, excluding account
identifiers and download URLs. `bytes_transferred` counts payload bytes written
in that invocation, including bytes rewritten during transfer retries;
`bytes_stored` in the run report includes retained pilot and partial files.

The source scale factor and delivered raster scale are distinct. Check
`values_already_scaled`, `delivered_scale` and `source_scale_factor` before using
NDVI or EVI; the source factor must not be applied again to scaled float values.
The raw schema is [vegetation_raw.yaml](../datasets/schemas/vegetation_raw.yaml).

The authenticated pilot on 2026-11-01 completed task
`be169e84-10c7-4d22-9752-54b049d143d6`: 34 verified files, 45,497,290 bytes
transferred and retained, and 389.34 seconds for the acquisition invocation.
The service recorded creation at 18:10:16.093702 and completion at
18:16:16.215345 UTC. The raster dimensions are 1,685 by 1,601, with
926.62543305 m pixels in the native sinusoidal CRS (sphere radius 6,371,007.181 m).
The executed pilot command was
`uv run --no-sync bolivia-fire vegetation pilot --workers 2 --timeout 3600` (exit 0).

| Layer | Delivered type | GDAL scale | Offset | Nodata |
| --- | --- | ---: | ---: | ---: |
| NDVI | int16 | 0.0001 | 0 | -3000 |
| EVI | int16 | 0.0001 | 0 | -3000 |
| VI Quality | uint16 | 1 | 0 | 65535 |
| Pixel reliability | int32 | 1 | 0 | -1 |
| Composite day of year | int16 | 1 | 0 | -1 |

NDVI and EVI stored values are unscaled integers, despite float32 discovery
metadata. Mask nodata before applying `physical = stored * 0.0001`.
Libraries that already apply the GDAL scale must not scale again.

The service included the preceding composite, starting on day 209, alongside
days 225, 241, 257 and 273. Five-layer raster bytes per composite were
8,873,295; 8,813,668; 9,020,274; 8,973,918; and 9,078,325, respectively.
The estimate used at full-run startup was 3,037,545,808 transfer bytes and
4,556,318,712 reserved storage bytes, including pilot-measured auxiliary files.
Boundary composites can add files beyond the nominal start-date count; the
storage margin and live disk checks remain necessary.
Annual bundles can repeat a boundary composite. Resolve overlaps by product,
layer, composite date and AOI; retain the bundle provenance and review differing
raster content before dropping a duplicate.

The completed acquisition covers all 16 annual partitions through 2026-07-31.
December 2011 and January through July 2026 are partial-year requests.

| Request year | Verified files | Verified bytes | Service seconds |
| --- | ---: | ---: | ---: |
| 2011 | 24 | 29,308,714 | 149.36 |
| 2012 | 129 | 220,116,647 | 985.66 |
| 2013 | 129 | 219,774,136 | 1,066.21 |
| 2014 | 129 | 222,410,576 | 1,722.30 |
| 2015 | 129 | 222,370,618 | 1,613.14 |
| 2016 | 129 | 219,642,738 | 2,242.56 |
| 2017 | 129 | 222,430,210 | 2,183.88 |
| 2018 | 129 | 224,673,533 | 1,711.35 |
| 2019 | 129 | 222,976,642 | 1,925.72 |
| 2020 | 129 | 218,479,709 | 1,855.07 |
| 2021 | 129 | 220,986,344 | 1,649.80 |
| 2022 | 129 | 216,949,058 | 2,248.55 |
| 2023 | 129 | 217,926,825 | 1,740.17 |
| 2024 | 129 | 220,826,003 | 1,749.92 |
| 2025 | 129 | 218,578,028 | 1,628.79 |
| 2026 | 84 | 136,493,683 | 1,281.22 |

Service seconds are AppEEARS completion minus creation times; queued requests
overlap, so these durations must not be summed as wall time.

Annual bundles contain 1,914 files and 3,253,943,464 bytes.
Including the pilot, retained payload is 1,948 files and
3,299,440,754 bytes. The resumed acquisition transferred
2,339,962,773 bytes across two invocations taking
6,419.56 seconds in total. Including the original
full attempt and pilot, acquisition invocations took 11,840.42 seconds;
time between invocations is excluded. Cumulative transferred bytes equal the
retained payload size.

An independent size and SHA-256 audit verified all 1,948 files in
4.71 seconds, with no partial or unmanifested payload files.
The audit observed 270,555,029,504 free bytes, above the 55 GiB floor.
Sanitization checks passed for 46 AppEEARS manifests.
The ledger contains one pilot and 16 annual task IDs, with no duplicate
submissions. The saved 2016 and 2017 IDs were reused. No rate-limit or abuse
response was observed during either resumed acquisition invocation.

Per-year evidence and totals are in
[appeears_resume_summary.json](../datasets/manifests/appeears_resume_summary.json),
with final task results in
[appeears_full.json](../datasets/manifests/appeears_full.json) and the independent
audit in [appeears_integrity.json](../datasets/manifests/appeears_integrity.json).
The earlier 1,800-second polling stop is preserved in
[appeears_resume_baseline.json](../datasets/manifests/appeears_resume_baseline.json).
The recovered Windows path-resolution stop is preserved in
[appeears_resume_attempt_01.json](../datasets/manifests/appeears_resume_attempt_01.json).

Validation on 2026-11-01:

- `uv run --no-sync ruff check .`: passed.
- `uv run --no-sync pytest tests/vegetation -q -p no:cacheprovider`: 33 passed.
- `uv run --no-sync python -m pytest -m 'not live and not local_data' -q -p no:cacheprovider`:
  578 passed, one skipped and three deselected in 162.49 seconds.
  Nine dependency warnings concern pandas deprecation behavior in DuckDB figure tests.

The module invocation makes the checkout root importable. The bare `pytest`
entry point fails collection in three landcover test modules that import
`tests.landcover` helpers; the module invocation runs those tests successfully.
