# Retrospective covariates

CHIRPS features and optional MapBiomas fractions join onto the strict panel by
keys. They do not change the target, strict features or partition assignment.
Definitions are in `datasets/schemas/covariates.yaml`.

## Availability audit

| Product | Version release | Latency and revisions | Use |
|---|---|---|---|
| CHIRPS v3.0 final daily `rnl`, ERA5-disaggregated | 2025-01-01, from the [provider version history](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/README-CHIRPSv3.0.txt) | Final data are normally completed in the third week of the following month; version changes can revise history. The exact first release of the daily `rnl` packaging is not stated on the product pages. | Retrospective only; the historical v3 record was unavailable through most study years. |
| MapBiomas Bolivia Collection 4 | 2026-10-01, from the [release announcement](https://bolivia.mapbiomas.org/2026/09/29/lanzamiento-de-la-coleccion-4-de-mapbiomas-bolivia/) | Annual maps; no fixed publication latency is promised. New collections update the full historical series and methodology. | Retrospective only; Collection 4 postdates all issue years. Live raster access is unavailable. |

The [CHIRPS daily documentation](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/readme.txt)
identifies ERA5 as `rnl` and IMERG Late V07 as `sat`. Only **final daily `rnl`**
is used. Daily values disaggregate pentadal totals; individual contributing
station observation periods need not match uniform UTC days. The lagged sums
therefore describe labelled antecedent daily amounts, not operationally
available observations at an exactly known local-day boundary.

The [MapBiomas product page](https://bolivia.mapbiomas.org/iniciativas-y-productos/cobertura-y-uso-del-suelo/cobertura/)
identifies Collection 4 as the latest 30 m Landsat collection, spanning
1985-2025. Class mappings follow its [Collection 4 legend](https://bolivia.mapbiomas.org/wp-content/uploads/sites/5/2026/09/Leyenda_Code_Bolivia_Col4.pdf),
including rocky outcrop 29 under non-forest formations, glacier 34 under water,
and salt flat 61 under non-vegetated land.

## Acquisition and restart

```powershell
uv sync --all-extras
uv run bolivia-fire covariates probe --product chirps --date 2024-01-01
uv run bolivia-fire covariates chirps --start 2024-01-01 --end 2024-12-31
uv run bolivia-fire covariates chirps --start 2012-01-01 --end 2026-01-04
uv run bolivia-fire covariates features
uv run bolivia-fire covariates summary
```

Repeat the same extraction command after an interruption. It validates cached
array hashes and final yearly checksums, reuses saved days, and atomically
extends completed partial years. `part.npz` is promoted before `complete.json`;
feature loading trusts only a completed, hash-valid year. Temporary day arrays
are removed only after successful promotion. A failed or interrupted year
retains its daily checkpoints. One command holds the extraction lock at a time.

Requests have concurrency one and a durable minimum three-second spacing. A
429 response uses `Retry-After` when present; otherwise cooldowns start at ten
minutes and double to at most sixty minutes, with positive jitter below the cap.
Six consecutive cooldowns open a circuit breaker and terminate the command.
The cooldown state survives process restarts; restarting does not bypass it.
A successful request clears consecutive cooldowns. Other transport failures
have three bounded attempts. Progress and cooldown records are printed with
flushing so redirected background logs remain useful.

Before each extraction or output batch, the code requires at least **55 GB of
free space on the output drive**, including room for the planned temporary
write. This replaces the earlier fixed transfer/storage ceilings. The default
output drive is D:. Do not run multiple independent extraction processes.

Only full-resolution raster windows covering `[-70,-23,-57,-9]` are read. A
local counting relay supplies `/vsicurl/` and forwards only bounded ranged GETs,
refusing a full-file HTTP 200 response. It derives file size from Content-Range
and caches the first range to serve GDAL's local HEAD/header requests without
another provider request. Overview metadata is inspected to verify the COG;
overview pixels are not read. Separate compressed full-resolution tiles can
require separate byte ranges. No global raster is persisted.

The native CHIRPS pixel width is `0.05000000074505806` degrees: it is close to,
but not exactly, the protocol's `0.05`. At Bolivia's western window edge the
native edge is about `1.64e-6` degrees east of the exact project edge; latitude
edges differ by about `1e-6` degrees. The extracted native window starts at
`(-70.04999836161733, -8.950001027435064)` and includes the entire requested
bbox. Project cell centers are mapped through the exact inverse native affine
transform. There is no interpolation or silent resampling. Records retain the
native transform, pixel-size errors and alignment flag. The first exploratory
record used a looser alignment tolerance; subsequent records use exact-scale
checks.

## Feature definitions

All windows end at Monday 00:00 UTC and exclude the issue day:

| Column | Definition |
|---|---|
| `rain_sum_7d`, `rain_sum_30d`, `rain_sum_90d` | Sum over `[issue-N days, issue)`, in mm. |
| `dry_days_30d` | Number of days with rainfall strictly below 1 mm in the 30-day window. |
| `rain_anomaly_90d` | The 90-day sum divided by the per-cell mean of complete reference windows in protocol training years, anchored on the issue's calendar month/day. |
| `rain_complete_7d`, `rain_complete_30d`, `rain_complete_90d` | All required daily values exist and are finite. |
| `rain_climatology_years` | Number of complete training-year reference windows used. |
| `rain_climatology_complete` | Every configured training-year reference is present. |

For training issues, reference windows ending after the issue are excluded so
changing rainfall on or after that issue cannot change any feature. Later issues
can use all training years. February 29 references use February 28 in non-leap
years. A missing day, source nodata tag, `-9999`, or nonfinite value remains
missing. Incomplete sums and dry-day counts are null. An absent or zero reference
mean gives a null anomaly. Partial reference coverage is counted and flagged;
it is never replaced with rainfall from validation or test years.

## Land cover and joining

```python
from bolivia_fire.covariates import join_retrospective

retrospective_table = join_retrospective(panel_table)
```

The function preserves panel row order and columns, and rejects duplicate
covariate keys. Rainfall joins `(cell_id, issue_date)`. If a fraction table is
present, issue year Y joins land-cover year Y-1. Missing source years produce
null feature values; absent product files add no columns.

The optional land-cover implementation assigns each source pixel center to a
half-open protocol grid cell, transforming coordinates to WGS84 when necessary.
It accumulates pixel counts in bounded 512-pixel batches within the Bolivia
bbox and normalizes six class groups by non-nodata pixel count. Unknown codes
and unobserved class 27 are retained in `other`; nodata 0 is excluded. These
are pixel-count fractions, not fractional polygon intersections.

The exact [official download page](https://bolivia.mapbiomas.org/downloads/mapas-para-descargar-geotiff/)
publishes the Collection 4 URL template recorded in `datasets/sources.yaml`.
On 2026-10-31, anonymous HEAD for 2023 and 2012 and a ranged GET for 2023
returned HTTP 403. The GET returned a 458-byte `AccessDenied` XML document;
it states that anonymous callers lack object access, or the object may not
exist. Three legacy primary portal paths returned 404 and supplied no verified
older public raster URL. No account, Earth Engine or credentials were used.
MapBiomas is therefore omitted from the acquired retrospective dataset. The
aggregation and Y-1 join remain tested for future accessible files.

## Artifacts and measured status

All paths below are under `datasets/processed/covariates/`:

- `chirps_daily/year=YYYY/part.npz`: float32 native window arrays, ISO dates,
  affine transform; a completed partial year records its exact bounds.
- `chirps_daily/year=YYYY/complete.json`: date bounds, product, byte count and
  compressed-file SHA256.
- `chirps_manifest.jsonl`: daily URL, remote size, transferred bytes, native
  transform, tiling/overview checks, nodata count and extracted-array SHA256.
- `chirps_features/year=YYYY/part.parquet`: unique cell-issue features; metadata
  records product, reference rule, training years, protocol hash and manifest
  snapshot hash. Rebuilding a partial year replaces that year's feature file.
- `http_requests.jsonl`, `chirps_errors.jsonl`, `access_state.json`,
  `request_gate.json`, `transfer_budget.json`: access accounting, retry evidence,
  cooldown and pacing checkpoints.
- `mapbiomas_fractions.parquet`: optional unique cell-year fractions; not
  produced from real data while the public URLs return 403.
- `last_*.json`, `acceptance_partial_2024.json`: command resources and acquisition
  status, separate from any predictive evaluation.

Measured partial extraction: 41 days, 2024-01-01 through 2024-02-10. Remote ranges
for committed windows total **70,267,042 bytes**; the transfer ledger totals
**80,147,052 bytes**, including unsuccessful-run prefetch and the 458-byte
MapBiomas probe. The compressed yearly checkpoint is **9,298,006 bytes**.
Checkpoint timestamps span **157.100 seconds**, approximately **0.261 saved
days/second** under the earlier unpaced policy. This is not a throughput promise
for the current polite-request policy. Offline checkpoint promotion took
**0.410 seconds**, with measured process peak working set **170.52 MiB**.
The initial one-day window probe took **4.836 seconds** with **140.58 MiB** peak.

Estimates from the 41 committed days: 366 daily windows would transfer about
0.627 GB and store about 0.083 GB compressed; the 5,118-day acquisition range
would transfer about 8.77 GB and store about 1.16 GB, before feature Parquet and
metadata. These are partial wet-season extrapolations, not full-year
measurements. The new pacing and potential server cooldowns make completion
time uncertain. No full-range run was started without the full-year acceptance
measurement.

The remainder of 2024 and the full 2012-2026 acquisition still need extraction
after CHC access recovers. Run the two extraction commands above, then build
features. A complete 2024 run and a real MapBiomas 2023 aggregation have not
passed live acceptance; their blockers are HTTP 429 and 403 respectively.

## Verification

```powershell
uv run ruff check src tests
uv run pytest -q
uv run pytest -q tests/covariates
```

Synthetic tests cover native index mapping and precision offsets, nodata,
full-resolution COG window reads without provider HEAD requests, byte-range
accounting, antecedent-window invariance, training-only anomaly references,
class fractions, previous-year joins, duplicate rejection, atomic restart and
extension, the free-space floor, cooldown persistence and circuit breaking.

Licence and attribution notes: [CHIRPS](../datasets/licenses/CHIRPS.md),
[MapBiomas](../datasets/licenses/MapBiomas.md),
[geoBoundaries](../datasets/licenses/geoBoundaries.md),
[FIRMS](../datasets/licenses/FIRMS.md).
