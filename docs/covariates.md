# Retrospective covariates

CHIRPS features and optional MapBiomas fractions join onto the strict panel by
keys. They do not change the target, strict features or partition assignment.
Definitions are in `datasets/schemas/covariates.yaml`.

## Availability audit

| Product | Version release | Latency and revisions | Use |
|---|---|---|---|
| CHIRPS v3.0 final daily `rnl`, ERA5-disaggregated | 2025-01-01, from the [provider version history](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/README-CHIRPSv3.0.txt) | Final data are normally completed in the third week of the following month; version changes can revise history. The exact first release of the daily `rnl` packaging is not stated on the product pages. | Retrospective only; the historical v3 record was unavailable through most study years. |
| MapBiomas Bolivia Collection 4 | 2026-10-01, from the [release announcement](https://bolivia.mapbiomas.org/2026/09/29/lanzamiento-de-la-coleccion-4-de-mapbiomas-bolivia/) | Annual maps; no fixed publication latency is promised. New collections update the full historical series and methodology. | Retrospective only. Polygon and partial raster pilots verified; full extraction paused on unknown request EECU. |

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

The legacy anonymous-raster implementation assigns each source pixel center to a
half-open protocol grid cell, transforming coordinates to WGS84 when necessary.
It accumulates pixel counts in bounded 512-pixel batches within the Bolivia
bbox and normalizes six class groups by non-nodata pixel count. Unknown codes
and unobserved class 27 are retained in `other`; nodata 0 is excluded. These
are pixel-count fractions, not fractional polygon intersections.

The exact [official download page](https://bolivia.mapbiomas.org/downloads/mapas-para-descargar-geotiff/)
publishes the Collection 4 URL template recorded in `datasets/sources.yaml`.
Anonymous access to the official 2012 and 2023 raster URLs returned HTTP 403
on 2026-10-31. No accessible public raster URL is recorded in the source registry.
MapBiomas is therefore omitted from the published retrospective dataset. The
legacy aggregation and Y-1 join remain tested for future accessible files.

### Authenticated Collection 4 extension

The separate extension follows `configs/extension_vegetation.yaml` and
`datasets/schemas/landcover.yaml`. On 2026-11-01, the official
[Earth Engine asset listing](https://bolivia.mapbiomas.org/downloads/assets-en-google-earth-engine/)
identified the public integration asset recorded under `landcover` in
`datasets/sources.yaml`. Authenticated metadata exposed 41 classification bands
for 1985-2025 on an EPSG:4326 grid with pixel width
`0.00026949458523585647` degrees, nominally 30 m. The selected native affine is
recorded in each pilot manifest; no categorical interpolation is applied.

The polygon pilots compute pixel-area sums by class on the server over each
project cell's intersection with the pinned national boundary. Results use direct
`computeFeatures` responses. Five fractions divide by mapped area; nodata,
unobserved code 27 and undeclared codes are excluded from that denominator and
retained in the area audit. Coverage below 0.95 makes every fraction null.
Missing response cells retain nulls. The
[Earth Engine weighting documentation](https://developers.google.com/earth-engine/guides/reducers_weighting)
specifies 256 levels for fractional boundary-pixel weights, so this is a native
grid weighted approximation to polygon intersection area. Very small national
cell parts require special attention in any future full-output audit.

The ledger uses a 150 EECU-hour Pacific-calendar-month project budget and a
30 EECU-hour workstream cap. API access, noncommercial registration and enabled
service state were checked directly. Community Tier and the absence of linked
billing were owner-attested. Month-to-date usage was read through the free
Cloud Console Monitoring route, and per-request EECU profiles were verified.
Unknown, stale or unreconciled usage pauses submissions. The private ledger is
paused; the raster follow-up's unknown request usage must be reconciled before
any computation can resume.

| 2012 request | Reported EECU-seconds | Response JSON bytes | Available cells |
|---|---:|---:|---:|
| Small 0.001-degree rectangle | 0.025 | 59 | Not a project-cell extraction |
| Geographically spread 100-cell pilot | 8.227 | 34,816 | 97/100 |
| Geographically spread 1,000-cell pilot | 278.067 | 325,544 | 989/1,000 |

All requested pilot cells were returned. Eleven cells in the larger pilot had
insufficient mapped coverage and retain null fractions. The maximum fraction
sum deviation among available cells was `2.220446049250313e-16`.
The grid has 37,146 cells; maps 2012-2025 require 520,044 cell-years. The larger
pilot's measured rate projects to **40.16863193 EECU-hours** before a safety
margin, exceeding the fixed 30-hour cap. The polygon method was stopped before
full-year extraction.
Polygon-pilot checksums, native-grid parameters and per-batch EECU profiles are in
`datasets/manifests/mapbiomas_pilot.jsonl`; outputs are under
`datasets/processed/landcover/`. These pilots do not satisfy H4/H5 feature
availability and do not change the published benchmark.

The raster alternative creates binary masks for the five declared groups and
invalid codes, applies the pinned national geometry at native resolution and
zero-fills outside national support. `reduceResolution(mean)` combines up to
65,536 native inputs per output pixel with `bestEffort=false`, followed by
`reproject` on the exact project grid. Binary masks and responses use float32.
Categorical overview sampling is not used. The final EPSG:4326 affine is
`[0.05, 0, -69.65, 0, -0.05, -9.65]`, width
244 and height 266; this is an integer offset of the registered global origin.

Direct `computePixels` NumPy arrays are downloaded serially in tiles of at most
16 cells on each side. Of the 272 bounding-grid tiles, 180 contain project
cells; the other 92 require no request. Each request uses the tile's local
intersection with the pinned national boundary, with a one-native-pixel halo
to retain overlapping pixels at tile edges. Each array is checkpointed with a
checksum before the next request. The local
normalization divides group means by their mapped sum, and multiplies the
mapped and invalid shares by each project's pinned geodesic national cell area.
Binary means measure degree-area overlap; the local area scaling approximates
native geodesic weighting within a 0.05-degree cell. Raster masking can also
differ from polygon boundary weights, especially in tiny national cell parts.
The predeclared pilot tolerance is 0.01 absolute for each class fraction and
mapped coverage, with matching availability on the 1,000 polygon-pilot cells.
Both the measured 14-year cost with a 1.5 safety factor and this agreement gate
must pass before another map year is requested. The request manifest retains
EECU profiles and failure evidence; the annual manifest retains arrays, compact
cell fractions, checksums and validation results.

The raster pilot stopped after 21 of 180 occupied tiles. These tiles cover
3,548 of 37,146 project cells and cost 716.890 rounded-upper EECU-seconds.
The next request did not provide a usable EECU profile. Total workstream usage
is therefore unknown, the request's reservation remains unreconciled, and
submissions are disabled. No full-country year was completed, no formal
14-year projection was established, and no later year was requested.

The partial comparison covers 95 of the 1,000 polygon-pilot cells. All 95 match
availability and pass the predeclared 0.01 absolute tolerance: maximum class
fraction error is 0.000129027 and maximum mapped-coverage error is 0.000046479.
This is partial validation and does not establish agreement on the remaining
cells. The partial Parquet retains every project-cell key, marks request status
explicitly and leaves unrequested or unknown fractions null. Its manifest is
`datasets/manifests/mapbiomas_raster_partial.jsonl`; the arrays and cell table
are `partial_raster_2012.npz` and `partial_raster_2012.parquet` under the
land-cover output directory. These outputs are not eligible for H4/H5 features.

The nominal reference for issue year Y is Y-1, with at most two years of age if
the greatest accessible eligible map year is older. Actual selected years must
be retained. Collection 4 was released on 2026-10-01; reference year does not
establish historical availability. Its
[ATBD](https://bolivia.mapbiomas.org/wp-content/uploads/sites/5/2026/10/ATBD_General_MapBiomas_Bolivia_COL4.pdf)
describes temporal rules using T+1 and T+2 and frequency filtering over the full
series. A one-year lag does not remove future information. The registered
extension remains retrospective and specifies no additional lag; an arbitrary
extra lag would not establish an operational historical vintage.

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

## Vegetation

The registered [vegetation contract](../configs/extension_vegetation.yaml)
defines the MOD13A2.061 screen, availability simulation and ten inputs.
[vegetation_features.yaml](../datasets/schemas/vegetation_features.yaml)
specifies the output paths, keys, columns and provenance.

```powershell
uv run --no-sync bolivia-fire vegetation-features --through-year 2015
uv run --no-sync bolivia-fire vegetation-features
```

The command uses only verified, complete downloaded bundles. It reads neither
panel labels nor model artifacts. It reads GeoTIFF integers with rasterio's
automatic scaling disabled, validates GDAL and tagged scale/offset metadata,
masks fill and invalid ranges, and applies the 0.0001 NDVI/EVI factor once.
Metadata-decoded float32 values are compared with that conversion. Every
composite has an adjacent scaling and rejection-count audit.

Weights use native pixel footprints with four segments per edge, transformed
to longitude/latitude and intersected with the national cell portion. National
edges are densified to at most 0.001 degree. Intersection areas use geodesic
WGS84 area. The denominator includes the complete national cell portion,
including rejected and missing pixels. Weights are cached by native grid,
boundary, cell grid and method hashes. This uses pixel areas rather than
pixel centres. The real audit covered all 37,146 cells, including 1,761 border
cells, with 1,788,133 intersections. Maximum relative area-sum error was
3.522259e-7; no cell had an uncovered area above the 1e-5 tolerance.
For ten evenly spaced border cells, increasing native edge segmentation from
four to sixteen changed the weights by at most 8.058452e-7 of national cell
area in summed absolute difference. The measured comparison is retained in
`datasets/processed/vegetation/weight_convergence.json`.

The saved acquisition used a 0.05-degree buffer; the contract specifies
0.1 degree. Each saved request polygon is checked to cover the entire pinned
national geometry, and the independent weight audit checks every national
cell portion. The buffer deviation and both coverage checks remain explicit
in source provenance; the registered contract is unchanged.

Composite starts are cross-checked against AppEEARS statistics and source
granule metadata. The exclusive end is the next 16-day boundary, capped at
January 1. Pixel observation dates come from the DOY layer and must lie
within that period. Availability requires end plus lag at or before issue.
Every contributing current observation must be at most 64 days old; change
references use the separate 128-day limit. Changes use the previous valid
available composite within 32 days and the nearest 48-day reference within
8 days, with earlier starts winning ties. Missing references stay null.

Cell climatologies exclude held-out observations and use only 2013-2020,
excluding the issue year for training issues. Geographic outputs use department
means, equally weighting non-held-out cells within each reference year and
then years. Both require four distinct valid reference years. No validation,
test or warm-up year enters the reference. Historical references pass their
own QA and coverage checks without the current-issue age gate. For 2026,
missing evidence of production and archive availability by July 31 makes
affected composites unavailable.

Features are separate for lags 16 and 32 and for temporal and geographic
climatologies. Files have unique `(cell_id, issue_date)` keys; joins reject
duplicates. Completed composite checksums are verified and reused as years
arrive. Feature snapshots are regenerated when their input or climatology
dependencies change. Absent acquisition years have no feature partition;
absent joins remain missing. Annual files for partial years remain provisional.

The initial 2011-2015 extraction processed 95 composites in 150.18 seconds,
peaked at 780,455,936 bytes of process-tree RSS, and retained 726,729,871 bytes.
Minimum free space was 273,212,698,624 bytes, above the 55 GiB floor.
With only three training reference years, complete feature support was zero;
anomalies remained null as required. Current-condition support was:

| Issue year | Cell-weeks per lag | Latest valid, lag 16 (%) | Latest valid, lag 32 (%) |
| --- | ---: | ---: | ---: |
| 2013 | 1,931,592 | 84.26 | 75.75 |
| 2014 | 1,931,592 | 80.61 | 72.26 |
| 2015 | 1,931,592 | 82.30 | 71.63 |

`datasets/processed/vegetation/report.json` contains monthly coverage,
missingness, resource measurements and output bytes for the latest build.
`acceptance_initial.json` retains the initial measurements above.

The extension through 2017 added 46 composites and reused all 95 earlier
composite files without changing their bytes or modification times. It took
100.61 seconds, peaked at 1,022,959,616 bytes RSS and retained 1,460,446,698 bytes;
minimum free space was 272,478,605,312 bytes. Added training reference years
required rebuilding seasonal anomalies in earlier feature partitions.
The measured extension is retained in `acceptance_extension.json`.
An unchanged repeat took 23.05 seconds, reused all 141 composites and 20
annual feature files, and preserved every Parquet hash and modification time.
The result is retained in `acceptance_idempotence.json`.

| Issue year | Complete temporal, lag 16 (%) | Complete temporal, lag 32 (%) | Complete geographic, lag 16 (%) | Complete geographic, lag 32 (%) |
| --- | ---: | ---: | ---: | ---: |
| 2013 | 30.76 | 29.55 | 65.13 | 60.20 |
| 2014 | 28.88 | 28.15 | 59.85 | 55.22 |
| 2015 | 29.55 | 27.86 | 58.92 | 53.09 |
| 2016 | 30.56 | 29.29 | 64.47 | 59.85 |
| 2017 | 32.18 | 29.22 | 62.49 | 55.24 |

Each percentage uses all 1,931,592 national cell-weeks in its issue year.
Temporal references are unavailable in held-out cells; geographic references
use non-held-out department observations. These are feature-support counts,
without labels or model evaluation.
