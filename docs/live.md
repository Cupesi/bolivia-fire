# Weekly estimate archive

## Status

Weekly estimates ended. [NASA stopped delivering Suomi NPP science data](https://www.earthdata.nasa.gov/data/alerts-outages/reminder-new-suomi-npp-observations-cease-november-2-2026) on 2 November 2026 at 15:40 UTC. The final issue is 2026-11-02, targeting 2 to 8 November 2026; that target week cannot be complete or verified. The model uses only S-NPP data. The archive contains 14 Monday issues from 3 August through 2 November 2026, with verification rows for 11 complete target weeks. All forecasts have `prospective=false`.

The archived product is labelled **Experimental weekly estimate (near-real-time data)**. It estimates whether a grid cell contains at least one selected satellite detection in a seven-day target interval. It is not a warning, an ignition probability, a count of fires, or an estimate of burned area.

## Data and model

Bootstrap read the frozen local VIIRS S-NPP standard-processing (SP) detection bundle through January 4, 2026, acquired SP observations for January 5 through July 31, and acquired near-real-time (NRT) observations from August 1 onward. Requests used the existing quota ledger, retries, response-size limit, credential redaction, and five-day maximum. Validated source responses and request identities are cached outside the live-data checkout; reruns verify hashes before reuse. Each request excluded the current UTC day because that day was incomplete.

The [FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) returned the 14-column S-NPP NRT schema with version `2.0NRT`. Unlike SP, it has no source-type column. The NRT proxy retains nominal/high-confidence rows outside 375 m great-circle buffers around all SP type-1 and type-2 locations observed from January 20, 2012, through July 31, 2026. The mask approximates the SP vegetation-source filter; unseen static sources can remain and nearby vegetation detections can be removed. The model card, metadata, index, forecasts, and observations disclose this proxy. Metadata records mask size and weekly removed row counts in the acquisition bounding box.

The frozen selected strict model is stored as LightGBM native text, with isotonic thresholds in JSON. Export verifies the original model, calibrator, and protocol hashes against the primary opening in `artifacts/evaluation/final_test_log.json`. No fitting, selection, or final-test evaluation ran during bootstrap or updates. Updates loaded the exported files; the large benchmark dataset and original serialized estimators are unnecessary. Calibration restores the frozen threshold dtype and uses the same interpolation path as the benchmark; full-issue prediction parity is checked exactly.

Live features use the benchmark's `DailyHistory` implementation. The rolling state contains 400 days of sparse selected-detection counts and FRP sums, daily acquisition/observation coverage, and a compact older-detection summary that preserves the 730-day recency cap and the no-prior-detection flag. A regression test compares all 21 strict features with saved SP panel rows for September 6 and September 13, 2021, using exact equality over all 37,146 cells.

The model was trained on SP detections and applied to NRT detections. SP-to-NRT equivalence has not been established. The strict model also covers geographic holdout cells in the live grid. Their missing cell climatology remains missing for model inference; the comparison baseline uses training department climatology for those cells.

## Timing and publication

An issue is Monday 00:00 UTC and its target is `[issue, issue + 7 days)`. Features use only acquisition times strictly before the issue. The former GitHub workflow was scheduled for Monday at 06:00 UTC to allow NRT processing of the previous day. Inputs excluded detections acquired after the issue, and verification covered the full seven-day target.

`prospective=true` means the scheduled GitHub workflow generated the forecast within 24 hours after the issue. Manual initialization and later forecasts are backfills, with `prospective=false`. Every forecast and verification row records `generated_utc` and `publication_lag_hours`. The archive displays all scored weeks in one verification table; it contains no prospective performance evidence.

Forecasts are immutable once written. Before retirement, repeating an update reused existing forecasts, replaced fetched daily state by date instead of accumulating it, and added missing outputs. Verification caught up every published forecast with a fully observed target, including forecasts stranded by missed weekly runs. Incomplete observations could become complete on a later update. A local lock prevents overlapping archive writers; workflow concurrency serializes deployments. The former workflow published outputs on the separate `live-data` branch. The manually dispatched `deploy-archive.yml` workflow builds the site from `main` and the `live-data` head and deploys GitHub Pages; it has no schedule, update job or FIRMS secret and does not write the data branch. It requires GitHub Pages with Actions and the `github-pages` deployment environment, and runs `npm run build:static` with `LIVE_DATA_DIR` and `PAGES_BASE=/` for `https://bolivia-fire.cupesi.org/`. The data branch is never merged into `main`. Build and deployment use separate jobs with read-only contents access for the build and Pages/id-token write access for deployment. Checkout credentials are not retained.

## Observation coverage and verification

Updates re-fetched the [MODAPS S-NPP outage list](https://modaps.modaps.eosdis.nasa.gov/services/production/outages_suomi_npp.html). A day is observed only when acquired and documented outage intervals cover strictly less than six UTC hours, using the union of overlapping intervals. Live coverage adds the permanent interval `[2026-11-02T15:40Z, infinity)` to the MODAPS intervals before applying that union. The final delivery day has 8 hours 20 minutes of outage and is unobserved; later days cannot be acquired or observed. The frozen benchmark coverage definition is unchanged. Missing or unobserved target days make every cell label null. Counts from the available days remain visible, but incomplete weeks are not scored. Successful empty responses establish acquisition, not proof of fire absence. A recorded score is withdrawn if refreshed outage coverage makes that target incomplete.

`verification/track_record.csv` reports AP, Brier score, ROC AUC, recall at 5% and 10% national area, equal-frequency calibration error, and training climatology AP on the same week and cells. Undefined metrics are empty CSV fields with a reason in `notes`. Area recall uses the benchmark's fractional boundary-cell allocation. Climatology uses cell estimates where available and department estimates otherwise. Weekly samples are small, cells are dependent, and a short record cannot establish reliability or a stable improvement.

## Retirement

From a source checkout with the existing Python environment and a separate local checkout of `live-data`, run:

```powershell
uv run --no-sync bolivia-fire live retire --out ../live-data --cache ../live-cache
```

The command fetches the public MODAPS outage list with a 4 MiB size limit and writes the validated response to `outages.html` in the selected cache. No credential, FIRMS request or inference is used. The command reapplies coverage to retained acquisition days and rewrites observations whose coverage changed, applying the withdrawal rule above. A refreshed list that reduces any recorded day's outage hours or changes an unobserved day to observed is rejected before archive writes; the error lists the affected dates. A second run with the same MODAPS response produces identical archive bytes. Both `live update` and `live bootstrap` refuse a retired archive before loading credentials or creating a cache. The command changes local files only; publication is separate.

`index.json` and `static/meta.json` contain the same `retirement` object:

- `status`: `"ended"`.
- `ended_utc`: the source delivery end, `"2026-11-02T15:40:00Z"`.
- `final_issue`: the last published forecast issue, `"2026-11-02"`.
- `reason`: a short source-delivery explanation.
- `source_url`: the NASA Earthdata alert linked above.

Existing publication timestamps remain unchanged. `outage_sha256` in metadata identifies the fetched MODAPS response used by retirement. The site reads the catalog's retirement fields; older catalogs without them retain the ongoing presentation.

Bootstrap formerly initialized the latest acquired issue and its preceding issue as labelled backfills. The default update issue was the latest Monday with an acquired 365-day input window.

## Historical backfill

```powershell
uv run --no-sync bolivia-fire live backfill --out ../live-data --cache ../live-cache --from 2026-08-03 --to 2026-10-12
```

The range is fixed before scoring. Every Monday in it receives a forecast; every complete target is scored, and incomplete targets retain null labels without a verification row.

The command uses the frozen local SP bundle, validated cached SP/NRT responses, the recorded MODAPS snapshot and the exported live model and calibrator. It makes no FIRMS request, loads no credential and performs no fitting or selection. Each input state ends before its issue, requires 365 acquired input days and retains the 730-day recency summary. Published rolling state is unchanged.

New forecasts must precede the earliest existing forecast and cannot pass the retirement's final issue. `index.json.backfill_batches` records file and verification-row hashes for each completed range; an identical rerun changes no bytes. `--check-parity` compares the same reconstruction with existing forecasts and the published state's feature path without writing outputs.

## Static geography and provenance refresh

After building the [place metadata](dataset.md#place-metadata), refresh an initialized live-data checkout without acquiring FIRMS observations or running inference:

```powershell
uv run --no-sync python -m bolivia_fire.live.static --out ../live-data
```

This static update reads existing grid order, writes `static/cell_places.json`, exports `static/boundaries_adm0.json` through `static/boundaries_adm3.json`, and completes `static/meta.json`. It does not write forecast, observation, state, model or verification files. Each display boundary is bounded to 1,500,000 JSON bytes, simplified with topology preservation, and includes a `simplification` object containing `tolerance_deg`, `preserve_topology` and `source_sha256`. Unmodified source geometry is used for cell assignments.

The place JSON is columnar, aligned exactly to `static/grid.json` cell IDs. Its `format` is `dictionary-v1`: `strings` contains unique strings shared by `department`, `province`, `municipality`, `nearest_place_name` and `nearest_place_feature_code`. Those five columns contain zero-based dictionary indices or null; an empty string remains distinct from null. All other columns retain their original values. Decoding restores [the schema](../datasets/schemas/cell_places.yaml) without rounding numbers or changing accents. The static client also accepts earlier flat exports; the API continues to read the original Parquet columns. `populated_places[i]` is an array of up to three objects with `name`, `geonames_id`, `feature_code` and `population`.

An earlier `live-data` commit retains the 5,853,252-byte (5.85 MB) `static/cell_places.json`; the current normalized export is below 5 MB.

Metadata retains live acquisition provenance and supplies `protocol` (`version`, `sha256`), `experiments_sha256`, `cells_sha256`, `sources_sha256`, `cell_places_sha256`, `model_id`, `model_name`, `source_model_sha256`, `source_calibrator_sha256` and `primary_opening_utc`. Its `dataset_snapshot_ids` contains the hashes plus `panel`, mapping evaluation panel years to the hashes recorded at the primary opening, and `evaluation_sources_sha256` for the original evaluation source registry. `sources_sha256` identifies the expanded display source registry; it is intentionally distinct from that frozen hash. The model and calibrator hashes are checked against the primary opening.

`source_attributions` and `attribution` include FIRMS, all four pinned geoBoundaries levels and GeoNames. CHIRPS is absent from strict live metadata because the strict model does not use rainfall covariates. The API also credits CHIRPS for its retrospective layers. No placeholder provenance values are exported. `boundary_simplification_tolerance_deg` maps each ADM level to the actual display tolerance in degrees.

## Reference snapshot

The snapshot initialized on October 31, 2026, occupied 14.4 MB, excluding Git metadata and the external raw-response cache. Its acquisition bootstrap used 61 area requests for 208 additional SP days and 91 closed NRT days, reserving 598 data transaction units.

| Operation | Wall time (seconds) |
|---|---:|
| Acquisition bootstrap | 77.190 |
| Regeneration from cached responses | 49.149 |
| October 26 issue update | 2.140 |

The mask contains 23,359 unique locations. It would remove 8,904 of 6,742,418 nominal/high SP type-0 rows in the bounding box: 0.132%. That snapshot contained 23 unobserved days, including five NRT dates. October 19's target week was complete; October 26's target week was incomplete.

The October 19 backfill contains 1,713 positive cells out of 37,146.
