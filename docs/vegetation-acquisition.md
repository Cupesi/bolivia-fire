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
task IDs before considering a submission. Each task has a six-hour polling
window, with intervals increasing from 300 to 900 seconds plus jitter. The
queue shrinks to two after an HTTP 429 or a persistent HTTP 403; already queued
tasks drain without cancellation. The current API reference states no concurrent
task limit. Local acquisition uses APIs and CLI commands only.

The documented commands use two download workers, `.part` files, HTTP Range when supported,
validated Content-Range headers, bundle byte counts and SHA-256 digests. A server
that ignores Range causes a fresh overwrite of the partial file. Verified files
are atomically promoted. Existing final files are checked before reuse.
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

Validation on 2026-11-01: `uv run --no-sync ruff check .` passed, and
`uv run --no-sync pytest -m 'not live and not local_data' -q -p no:cacheprovider`
passed with 450 tests, one skip and three deselections. Nine dependency warnings
concerned pandas deprecation behavior in existing DuckDB figure tests.
The vegetation suite contains 28 synthetic tests, including token expiration,
401/403 refresh, malformed responses, task errors, incomplete bundles, checksum
mismatch, interrupted-stream recovery, ignored Range responses, restart
idempotence, safe exceptions and delivered scaling inspection.

The full acquisition attempt used
`uv run --no-sync bolivia-fire vegetation full --workers 2 --timeout 1800`.
It ran from 18:18:49.009837 to 19:42:40.527710 UTC on 2026-11-01
(5,031.52 seconds) and exited with status 1 after the per-task polling timeout
while waiting for the 2016 bundle. AppEEARS still reported the saved 2016 and
2017 tasks as processing at the subsequent status check. Local acquisition and
prefetch processes were stopped; the service requests remain available for resume.

Five annual request partitions, covering December 2011 and calendar years
2012 through 2015, were downloaded: 540 files and 913,980,691 bytes transferred.
Including the pilot, retained payload is 574 files and 959,477,981 bytes.
An independent SHA-256 audit verified every retained file against its saved
bundle record in 4.67 seconds; see
[appeears_integrity.json](../datasets/manifests/appeears_integrity.json).
The full-run report recorded 274,382,180,352 free bytes at stop, above the
55 GiB floor. This stop was caused by the polling limit, not storage capacity.

Eleven annual partitions remain, for 2016 through July 2026. The saved tasks are
`6fc8ee32-a8b8-41b6-93c2-a8052a5cfef9` (2016) and
`83f8b720-2e9e-4bb0-aa48-b49e16d7746b` (2017).
Resume with the documented `vegetation full` command and its 21,600-second
per-task timeout; existing verified files and submitted task IDs are reused.
The measured run and provider snapshot are in
[appeears_full.json](../datasets/manifests/appeears_full.json) and
[appeears_stop.json](../datasets/manifests/appeears_stop.json).

Changed paths comprise `src/bolivia_fire/vegetation/`,
`src/bolivia_fire/vegetation_cli.py`, its registration in `src/bolivia_fire/cli.py`,
`tests/vegetation/`, the appended vegetation source
entry, the MODIS license note, the raw schema, this document and the sanitized
AppEEARS manifests. Final verification repeated the required test command with
450 passed, one skipped and three deselected in 116.62 seconds; Ruff passed.
