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
uv run --no-sync bolivia-fire covariates probe --product chirps --date 2024-01-01
uv run --no-sync bolivia-fire covariates chirps --start 2024-01-01 --end 2024-12-31
uv run --no-sync bolivia-fire covariates chirps --start 2012-01-01 --end 2026-01-04
uv run --no-sync bolivia-fire covariates chirps-annual --start-year 2012 --end-year 2026
uv run --no-sync bolivia-fire covariates chirps-annual --start-year 2018 --end-year 2026 --transport ftp
uv run --no-sync bolivia-fire covariates features
uv run --no-sync bolivia-fire covariates summary
```

Repeat the same extraction command after an interruption. It validates cached
array hashes and final yearly checksums, reuses saved days, and atomically
extends completed partial years. `part.npz` is promoted before `complete.json`;
feature loading trusts only a completed, hash-valid year. Temporary day arrays
are removed only after successful promotion. A failed or interrupted year
retains its daily checkpoints. One command holds the extraction lock at a time.

The annual command uses the same final daily `rnl` product from the provider's
[annual NetCDF directory](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/netcdf/byYear/).
It checks the HTTPS listing and exact HEAD Content-Length, then downloads one
file at a time into `datasets/.tmp/*.nc.part`. Interrupted transfers resume with
Range and If-Range against the saved URL, size and a matching strong ETag or
Last-Modified value. A partial file without an entity validator is restarted.
The disk check reserves
the remaining download size above the 55 GB floor. A completed year is reused
only if its remote identity and local compressed output checksum still match.

HTTP 429 switches the remaining annual range to passive FTP; `--transport ftp`
selects it explicitly. On 2026-10-31 the verified FTP directory was
`ftp://ftp.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/netcdf/byYear/`.
The `/pub/org/chc/` prefix in the provider's older FTP examples returned 550.
FTP verifies listing names, binary SIZE and MDTM before each transfer, resumes
only against a matching modification identity, and checks final bytes and
SHA256 before extraction. Both transports remain
sequential, with one annual file retained at a time.

Each annual file is read on its native grid without interpolation, reversing
ascending latitude storage to match COG rows. Before promotion, every cached
daily COG window in that year is compared cell by cell, including missingness;
the tolerance is one float32 spacing. A discrepancy stops extraction and keeps
both the old windows and the annual download. Successful extraction appends the
annual URL, bytes, SHA256, per-day window checksums, comparison count and maximum
absolute difference to `chirps_annual_manifest.jsonl`, then deletes the download
before starting the next year. Daily outputs and feature loading are shared
with the COG command. For 2026 only January 1-4 are extracted; COGs are used if
the annual file is absent from the provider listing.

Every coordinate centre is validated against nominal CHIRPS centres or the
provider's float32 endpoint-and-step arithmetic, within half a coordinate ulp.
The 2014 annual file's arrays exactly matched that arithmetic: the longitude
step was float32 `0.05`, and the latitude step was float32 `0.049999997`.
Maximum deviations from ideal decimal centres were `1.220703126e-5` degrees
longitude and `9.155273446e-6` degrees latitude. These coordinate encodings do
not change pixel indices: extraction uses the exact native COG affine.
Both `_FillValue` and `missing_value` are masked before rejecting other negative
rainfall values.

COG requests have concurrency one and a durable minimum three-second spacing. A
429 response uses `Retry-After` when present; otherwise cooldowns start at ten
minutes and double to at most sixty minutes, with positive jitter below the cap.
Six consecutive cooldowns open a circuit breaker and terminate the command.
The cooldown state survives process restarts; restarting does not bypass it.
A successful request clears consecutive cooldowns. Other transport failures
have three bounded attempts. Progress and cooldown records are printed with
flushing so redirected background logs remain useful.

Before each extraction or output batch, the code requires at least **55 GB of
free space on the output drive**, including room for the planned temporary
write. Do not run multiple independent extraction processes against the same
output directory.

The COG command reads full-resolution windows covering `[-70,-23,-57,-9]`. A
local counting relay supplies `/vsicurl/` and forwards only bounded ranged GETs,
refusing a full-file HTTP 200 response. It derives file size from Content-Range
and caches the first range to serve GDAL's local HEAD/header requests without
another provider request. Overview metadata is inspected to verify the COG;
overview pixels are not read. Separate compressed full-resolution tiles can
require separate byte ranges. No global COG is persisted.

The native CHIRPS pixel width is `0.05000000074505806` degrees: it is close to,
but not exactly, the protocol's `0.05`. At Bolivia's western window edge the
native edge is about `1.64e-6` degrees east of the exact project edge; latitude
edges differ by about `1e-6` degrees. The extracted native window starts at
`(-70.04999836161733, -8.950001027435064)` and includes the entire requested
bbox. Project cell centers are mapped through the exact inverse native affine
transform. There is no interpolation or silent resampling. Records retain the
native transform, pixel-size errors and alignment flag. Extraction requires
exact-scale checks.

## Feature definitions

All windows end at Monday 00:00 UTC and exclude the issue day:

| Column | Definition |
|---|---|
| `rain_sum_7d`, `rain_sum_30d`, `rain_sum_90d` | Sum over `[issue-N days, issue)`, in mm. |
| `dry_days_30d` | Number of days with rainfall strictly below 1 mm in the 30-day window. |
| `rain_anomaly_90d` | The 90-day sum divided by the per-cell mean of complete reference windows in protocol training years, anchored on the issue's calendar month/day. |
| `rain_complete_7d`, `rain_complete_30d`, `rain_complete_90d` | All required daily values exist and are finite. |
| `rain_climatology_years` | Number of complete training-year reference windows used. |
| `rain_climatology_complete` | Every applicable training-year reference is present: seven for training issues, eight for later issues. |

Training issues use reference windows from the other protocol training years
(2013-2020), including later training years, following the protocol's
leave-one-year-out climatology exception. Validation and test issues use all
eight training years. Validation and test rainfall never enters the reference
mean. The antecedent sums and dry-day counts always exclude the issue day and
later days. February 29 references use February 28 in non-leap years.
A missing day, source nodata tag, `-9999`, or nonfinite value remains
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
Anonymous access to the official 2012 and 2023 raster URLs returned HTTP 403
on 2026-10-31. No accessible public raster URL is recorded in the source registry.
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
- `chirps_annual_manifest.jsonl`: annual URL, exact source bytes, downloaded-file
  SHA256, per-day window hashes, coordinate validation and COG comparisons.
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

The annual acquisition is complete: **5,118 days**, 2012-01-01 through
2026-01-04, on the native **281 x 261** window. The 15 annual source files
totalled **59,446,803,959 bytes** and were deleted after extraction. Retained
daily archives total **960,037,821 bytes**. There are no missing rainfall values
or annual staging files. Source identities and daily window checksums are retained
in the acquisition manifests.

| Year | Days | Annual source bytes | Runtime (s) | Daily archive bytes |
|---|---:|---:|---:|---:|
| 2012 | 366 | 4,034,816,331 | 406.58 | 69,989,405 |
| 2013 | 365 | 4,017,791,358 | 478.55 | 69,566,182 |
| 2014* | 365 | 4,031,104,175 | 15.22 | 71,158,783 |
| 2015 | 365 | 4,635,815,277 | 611.95 | 77,300,241 |
| 2016 | 366 | 4,068,923,402 | 391.42 | 65,914,054 |
| 2017 | 365 | 4,027,896,733 | 352.01 | 69,943,074 |
| 2018 | 365 | 4,061,749,369 | 361.26 | 71,294,546 |
| 2019 | 365 | 4,019,840,128 | 331.55 | 68,093,426 |
| 2020 | 366 | 3,868,969,181 | 310.92 | 64,128,340 |
| 2021 | 365 | 3,927,181,528 | 402.33 | 68,369,210 |
| 2022 | 365 | 4,000,747,684 | 358.86 | 63,159,824 |
| 2023 | 365 | 4,012,981,608 | 382.56 | 66,792,532 |
| 2024 | 366 | 3,895,097,852 | 276.65 | 64,477,251 |
| 2025 | 365 | 3,877,714,018 | 324.21 | 68,868,722 |
| 2026 | 4 | 2,966,175,315 | 260.25 | 982,231 |

The 2014 runtime covers resume and extraction of a completed download; its
initial transfer duration is unavailable. Source bytes and SHA256 are retained.
HTTPS supplied 2012-2017 and FTP supplied 2018-2026.

All **188 pre-existing COG days** matched the NetCDF windows exactly:
147 in 2012 and 41 in 2024, with maximum absolute difference **0.0**.
The native-grid source comparisons are recorded in annual_cog_validation.json.

The protocol feature build produced **25,184,988 unique cell-issue rows** for
37,146 cells and 678 Monday issues, 2013-01-07 through 2025-12-29. The 2012
rainfall is warm-up input. All 7/30/90-day windows and applicable climatology
references are complete, with no null rainfall anomalies.

| Issue year | Rows | Complete 7/30/90-day windows (%) | Climatology complete (%) | Reference years |
|---|---:|---:|---:|---:|
| 2013 | 1,931,592 | 100/100/100 | 100 | 7 |
| 2014 | 1,931,592 | 100/100/100 | 100 | 7 |
| 2015 | 1,931,592 | 100/100/100 | 100 | 7 |
| 2016 | 1,931,592 | 100/100/100 | 100 | 7 |
| 2017 | 1,931,592 | 100/100/100 | 100 | 7 |
| 2018 | 1,968,738 | 100/100/100 | 100 | 7 |
| 2019 | 1,931,592 | 100/100/100 | 100 | 7 |
| 2020 | 1,931,592 | 100/100/100 | 100 | 7 |
| 2021 | 1,931,592 | 100/100/100 | 100 | 8 |
| 2022 | 1,931,592 | 100/100/100 | 100 | 8 |
| 2023 | 1,931,592 | 100/100/100 | 100 | 8 |
| 2024 | 1,968,738 | 100/100/100 | 100 | 8 |
| 2025 | 1,931,592 | 100/100/100 | 100 | 8 |

Feature construction took **127.788 seconds**, with
**2,042.35 MiB** peak working set. Feature Parquet files
total **598,171,516 bytes**.
The FTP acquisition process peaked at **598.72 MiB**. The 55 GB free-space
floor remained enforced before downloads and output writes.

Measured audit details are in annual_daily_validation.json and
annual_feature_validation.json. MapBiomas remains unavailable through the
verified anonymous URLs and is omitted from the retrospective dataset.

The stored covariate tree totals **1,563,039,121 bytes**, including
daily archives, features, manifests, access logs and audit records.
The resource summary is acceptance_annual.json.

## Verification

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync pytest -q
uv run --no-sync pytest -q tests/covariates
```

Synthetic tests cover native index mapping and precision offsets, nodata,
full-resolution COG window reads without provider HEAD requests, byte-range
accounting, antecedent-window invariance, training-only anomaly references,
class fractions, previous-year joins, duplicate rejection, atomic restart and
extension, the free-space floor, cooldown persistence and circuit breaking.
Annual NetCDF fixtures cover leap-day indexing, both CF missing-value tags,
every coordinate centre, provider float32 arithmetic, HTTP/FTP resume and
protocol handoff, source-identity refusal, deletion after extraction, HTTP 429
fallback and completed-year reuse. Anomaly oracles check seven/eight reference
years and invariance to validation/test rainfall changes for training issues.

Licence and attribution notes: [CHIRPS](../datasets/licenses/CHIRPS.md),
[MapBiomas](../datasets/licenses/MapBiomas.md),
[geoBoundaries](../datasets/licenses/geoBoundaries.md),
[FIRMS](../datasets/licenses/FIRMS.md).
