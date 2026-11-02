# Retrospective covariates

CHIRPS features and optional MapBiomas fractions join onto the strict panel by
keys. They do not change the target, strict features or partition assignment.
Definitions are in `datasets/schemas/covariates.yaml`.

## Availability audit

| Product | Version release | Latency and revisions | Use |
|---|---|---|---|
| CHIRPS v3.0 final daily `rnl`, ERA5-disaggregated | 2025-01-01, from the [provider version history](https://data.chc.ucsb.edu/products/CHIRPS/v3.0/README-CHIRPSv3.0.txt) | Final data are normally completed in the third week of the following month; version changes can revise history. The exact first release of the daily `rnl` packaging is not stated on the product pages. | Retrospective only; the historical v3 record was unavailable through most study years. |
| MapBiomas Bolivia Collection 4 | 2026-10-01, from the [release announcement](https://bolivia.mapbiomas.org/2026/09/29/lanzamiento-de-la-coleccion-4-de-mapbiomas-bolivia/) | Annual maps; no fixed publication latency is promised. New collections update the full historical series and methodology. | Retrospective only. National maps for 2012-2025 and issue features for 2013-2026 are available in the separate extension. |

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

The primary retrospective panel uses `join_retrospective(panel_table)` to join
CHIRPS by `(cell_id, issue_date)`, preserving panel row order and rejecting
duplicate keys. It contains no MapBiomas inputs: anonymous official raster
access failed before the primary analysis. The legacy optional join uses
issue year Y-1 and six pixel-count groups, including `other`; it is separate
from the five-feature land-cover analysis below.

### Completed Collection 4 features

The [separately specified covariate protocol](extension-vegetation.md) and
[configuration](../configs/extension_vegetation.yaml) define five inputs:

| Feature | Collection 4 class codes |
|---|---|
| `mapbiomas_forest_fraction` | 1, 3, 4, 6 |
| `mapbiomas_nonforest_fraction` | 10, 11, 12, 13, 29, 63, 66, 77, 81, 82 |
| `mapbiomas_farming_fraction` | 14, 15, 18, 21, 39, 40, 72 |
| `mapbiomas_nonvegetated_fraction` | 22, 23, 24, 25, 30, 61, 68, 92 |
| `mapbiomas_water_fraction` | 26, 31, 33, 34 |

Each fraction divides its class area by total mapped area in the national
portion of the cell. Nodata, unobserved code 27 and undeclared codes are
excluded from mapped area. Mapped coverage must be at least 0.95 of national
cell area; otherwise all five fractions are null. Available fractions must
lie in [0, 1] and sum to one within 1e-6. Missing cells and failed coverage
retain nulls; there is no sixth model input or zero-filled vector.

For issue year Y, select the greatest accessible map year at most Y-1,
with maximum reference age two years. Retain nominal year, actual year and
reference age. The completed 2013-2026 feature build uses maps for 2012-2025;
an unavailable cell does not fall back to an older map. The one-year lag
does not establish availability at historical issue time. Collection 4
reprocesses history with temporal filtering, so these features remain
retrospective.

### Acquisition and aggregation evidence

Authenticated Earth Engine access to the official public Collection 4 asset
completed all 180 occupied native-grid tiles per year for 2012-2025 through
the zero-cost Community Tier route. The final Monitoring total was
**22.87 EECU-hours**, including earlier pilots,
failed attempts and diagnostic overhead. A separate 0.1-hour reserve remained
within the authorized 100-hour workstream cap. Read-only billing verification
recorded no enabled billing or linked billing account. Source identity,
licence, access evidence and costs are retained in the
[source manifest](../datasets/manifests/mapbiomas_source.jsonl) and
[Monitoring audit](../datasets/manifests/mapbiomas_monitoring_audit.jsonl).

The raster method reduces native class masks to the exact project grid
without categorical interpolation. It measures degree-area overlap,
normalizes class groups over mapped coverage, and scales areas by the pinned
geodesic national cell area. It approximates native pixel/polygon geodesic
intersection weighting. Against the independent 1,000-cell polygon pilot,
989 cells were jointly available, with no availability disagreement;
maximum absolute errors were 0.000653379663364 for class fractions and
0.000245167413933 for coverage, below the declared 0.01 tolerance.
These pilot measurements do not establish a national error bound. The
[land-cover amendment](extension-landcover-amendment.md) specifies this
estimator while retaining the base fraction definitions.

The annual outputs contain 520,044 cell-year rows, including 515,886 available
rows. Canonical maps are `datasets/processed/landcover/mapbiomas_YYYY.parquet`
and `raster_YYYY.npz`; issue features are under `landcover/features/year=YYYY/`
and combined inputs under `landcover/combined/lag=L/geography=G/year=YYYY/`.
Feature construction uses cell identifiers and the Monday calendar without
labels. Combined files preserve the vegetation snapshot, receipt hashes and
unavailable 2026 confirmation values. The
[feature manifest](../datasets/manifests/mapbiomas_features.jsonl) records
output hashes and missingness; the Monitoring audit links six registered
land-cover runs with `evaluated_eligible` feature status. This establishes
input availability only. Six H4/H5 fits are complete under the dated amendment;
both primary validation decisions are inconclusive, and test-year scores are exploratory.
Confirmation was refused without opening labels. Measured findings are in
[environmental covariate results](results.md#environmental-covariates).

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
- `mapbiomas_fractions.parquet`: legacy optional cell-year output, absent from the original
  rainfall comparison after anonymous URLs returned 403. Later authenticated Collection 4
  aggregates are under `datasets/processed/landcover/`, as documented above.
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
annual_feature_validation.json. MapBiomas was unavailable through the verified anonymous
URLs for the original rainfall comparison. Authenticated Collection 4 aggregates and
H4/H5 issue features are local under `datasets/processed/landcover/`; the
[versioned release format](../datasets/README.md#versioned-data-release) includes them.

The stored covariate tree totals **1,563,039,121 bytes**, including
daily archives, features, manifests, access logs and audit records.
The resource summary is acceptance_annual.json.

## Verification

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync python -m pytest -q
uv run --no-sync python -m pytest -q tests/covariates
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

The completed acquisition build retained 339 unique composites: three in
2011, 23 in each year 2012-2025, and 14 in 2026. The acquisition includes the
preceding composite starting 2011-11-17; the final start is 2026-07-28.
All 141 existing composites retained their hashes and modification times;
198 new composites were aggregated. All 56 annual feature partitions were
written, including refreshed anomalies in the 20 existing partitions. Every
2013-2020 reference year now supplies all 23 slots. Training issues exclude
their own year, leaving seven possible reference years; later issues use
eight. Each cell or department reference still requires four valid years.

The build took 450.97 seconds, peaked at 1,650,724,864 bytes of process-tree
RSS, and measured 268,112,646,144 minimum free bytes. At build completion,
the output tree contained 3,891,569,765 bytes, before later audit records.
Composite files and receipts occupy 164,368,010 bytes, features and receipts
3,713,233,233 bytes, and cached weights and their receipt 13,025,477 bytes.
The full-build measurements remain in `all_years_build.json` independently
of the latest-run `report.json`.

An independent audit checked all 105,048,888 feature rows, their unique
cell-issue support, metadata, latest available composite, both change
references, coverage and age gates, and both seasonal anomalies. It
reconstructed climatologies directly from the annual aggregates and verified
652 monthly lag/geography coverage records and all ten finite-value counts.
For each of the 339 real composite periods and both lags, a synthetic
one-cell series checked the nanosecond before availability, the exact
boundary and the first eligible Monday: 2,034 boundary assertions passed.
The audit took 65.75 seconds and peaked at 678,793,216 bytes RSS.
`all_years_audit.json` retains monthly composite and feature coverage,
per-feature missingness counts, annual summaries and resource measurements.

The following anomaly percentages use every national cell-week in each
lag/geography combination. NDVI and EVI anomaly availability counts agree.
Geographic anomaly support equals latest-condition support in this build.

| Issue year | Cell-weeks per combination | Anomaly temporal 16 (%) | Anomaly temporal 32 (%) | Anomaly geographic 16 (%) | Anomaly geographic 32 (%) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2013 | 1,931,592 | 54.46 | 50.31 | 84.26 | 75.75 |
| 2014 | 1,931,592 | 54.05 | 49.99 | 80.61 | 72.26 |
| 2015 | 1,931,592 | 55.38 | 49.34 | 82.30 | 71.63 |
| 2016 | 1,931,592 | 56.22 | 51.45 | 86.76 | 78.06 |
| 2017 | 1,931,592 | 56.45 | 50.43 | 84.42 | 73.83 |
| 2018 | 1,968,738 | 54.03 | 49.80 | 81.69 | 72.87 |
| 2019 | 1,931,592 | 55.29 | 49.44 | 81.02 | 70.61 |
| 2020 | 1,931,592 | 53.93 | 49.30 | 82.06 | 73.38 |
| 2021 | 1,931,592 | 57.82 | 52.75 | 83.59 | 74.79 |
| 2022 | 1,931,592 | 57.76 | 52.89 | 84.32 | 75.39 |
| 2023 | 1,931,592 | 57.02 | 52.78 | 85.37 | 77.00 |
| 2024 | 1,968,738 | 53.12 | 47.71 | 79.27 | 70.43 |
| 2025 | 1,931,592 | 58.21 | 54.33 | 88.21 | 80.85 |
| 2026 | 1,077,234 | 0.00 | 0.00 | 0.00 | 0.00 |

Complete support additionally requires both changes, valid fraction and age:

| Issue year | Complete temporal 16 (%) | Complete temporal 32 (%) | Complete geographic 16 (%) | Complete geographic 32 (%) |
| --- | ---: | ---: | ---: | ---: |
| 2013 | 45.38 | 42.62 | 65.18 | 60.24 |
| 2014 | 41.87 | 39.36 | 60.03 | 55.34 |
| 2015 | 41.83 | 38.15 | 58.94 | 53.10 |
| 2016 | 44.43 | 41.56 | 64.52 | 59.89 |
| 2017 | 44.74 | 39.80 | 62.49 | 55.24 |
| 2018 | 43.79 | 40.25 | 61.87 | 55.93 |
| 2019 | 41.19 | 36.77 | 55.77 | 49.52 |
| 2020 | 44.74 | 41.83 | 62.48 | 57.98 |
| 2021 | 46.36 | 42.26 | 63.42 | 57.44 |
| 2022 | 48.35 | 43.83 | 64.87 | 58.25 |
| 2023 | 48.00 | 45.01 | 68.16 | 63.06 |
| 2024 | 41.35 | 37.85 | 57.66 | 52.54 |
| 2025 | 49.19 | 45.51 | 69.64 | 63.89 |
| 2026 | 0.00 | 0.00 | 0.00 | 0.00 |

All 29 confirmation Mondays, 2026-01-05 through 2026-07-20, are present.
All ten vegetation features are null for their 1,077,234 rows per combination:
none of the source records proves archive availability before the registered
July 31 cutoff. Complete acquisition does not establish that evidence.
The 2026 composites remain retained with their QA, scaling and coverage
audits, while confirmation vegetation remains unavailable. No labels were
read and no models were trained.

The unchanged full rerun took 106.71 seconds, peaked at 863,821,824 bytes RSS
and reused all 339 composites and 56 feature partitions. All 1,131 retained
composite, feature and weight files, including receipts, preserved their
hashes and modification times. The 395 Parquet files total 3,877,107,516
bytes. Minimum free space across build, audit and rerun was
268,111,671,296 bytes. Evidence is in `all_years_idempotence.json`; the combined
acceptance report is `all_years_acceptance.json`.

Validation: `uv run --no-sync ruff check .` passed;
`uv run --no-sync python -m pytest tests/vegetation_features -q -p no:cacheprovider`
passed all 22 tests; and
`uv run --no-sync python -m pytest -m 'not live and not local_data' -q -p no:cacheprovider`
finished with 583 passed, one skipped and three deselected in 144.02 seconds.
Nine dependency warnings concerned pandas behavior in the figure tests.
