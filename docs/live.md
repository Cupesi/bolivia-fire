# Weekly live estimates

The site labels this product **Experimental weekly estimate (near-real-time
data)**. It estimates whether a grid cell contains at least one selected
satellite detection in a seven-day target interval. It is not a warning, an
ignition probability, a count of fires, or an estimate of burned area.

## Data and model

Bootstrap reads the frozen local VIIRS S-NPP standard-processing (SP) detection
bundle through January 4, 2026, acquires SP observations for January 5 through
July 31, and acquires near-real-time (NRT) observations from August 1 onward.
Requests use the existing quota ledger, retries, response-size limit, credential
redaction, and five-day maximum. Validated source responses and request identities
are cached outside the live-data checkout; reruns verify hashes before reuse.
The current UTC day is excluded because its observations are incomplete.

The [FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) currently
returns the 14-column S-NPP NRT schema with version `2.0NRT`. Unlike SP, it has no
source-type column. The NRT proxy retains nominal/high-confidence rows outside
375 m great-circle buffers around all SP type-1 and type-2 locations observed
from January 20, 2012, through July 31, 2026. The mask approximates the SP
vegetation-source filter; unseen static sources can remain and nearby vegetation
detections can be removed. The model card, metadata, index, forecasts, and
observations disclose this proxy. Metadata records mask size and weekly removed
row counts in the acquisition bounding box.

The frozen selected strict model is exported as LightGBM native text, with
isotonic thresholds in JSON. Export verifies the original model, calibrator, and
protocol hashes against the primary opening in
`artifacts/evaluation/final_test_log.json`. No fitting, selection, or final-test
evaluation runs during bootstrap or updates. Updates load the exported files;
the large benchmark dataset and original serialized estimators are unnecessary.
Calibration restores the frozen threshold dtype and uses the same interpolation
path as the benchmark; full-issue prediction parity is checked exactly.

Live features use the benchmark's `DailyHistory` implementation. The rolling
state contains 400 days of sparse selected-detection counts and FRP sums, daily
acquisition/observation coverage, and a compact older-detection summary that
preserves the 730-day recency cap and the no-prior-detection flag. A regression
test compares all 21 strict features with saved SP panel rows for September 6
and September 13, 2021, using exact equality over all 37,146 cells.

The model was trained on SP detections and is applied to NRT detections; the
track record measures how well it holds up. SP-to-NRT equivalence has not been
established. The strict model also covers geographic holdout cells in the live
grid. Their missing cell climatology remains missing for model inference; the
comparison baseline uses training department climatology for those cells.

## Timing and publication

An issue is Monday 00:00 UTC and its target is `[issue, issue + 7 days)`.
Features use only acquisition times strictly before the issue. The GitHub
workflow runs Monday at 06:00 UTC, allowing NRT processing of the previous day.
It normally publishes about six hours into its target week, excludes detections
from those hours from its inputs, and scores the full seven-day target.

`prospective=true` means the scheduled GitHub workflow generated the forecast
within 24 hours after the issue. Manual initialization and later forecasts are
backfills, with `prospective=false`. Every forecast and verification row records
`generated_utc` and `publication_lag_hours`. Performance summaries must use only
prospective rows and display backfills separately. The initialized October 19
and October 26, 2026, forecasts are backfills.

Forecasts are immutable once written. Repeating an update reuses existing
forecasts, replaces fetched daily state by date instead of accumulating it, and
adds missing outputs. Verification catches up every published forecast with a
fully observed target, including forecasts stranded by missed weekly runs.
Incomplete observations can become complete on a later
update. Bootstrap refuses an archive containing forecasts; use `live update`
after initialization. A local lock and workflow concurrency prevent overlapping
writers. The workflow publishes outputs on the separate `live-data` branch,
builds the site from `main` plus those outputs, and deploys GitHub Pages. The
data branch is never merged into `main`.
Update, build, and deployment use separate jobs with the permissions each needs.
Checkout credentials are not retained; the push step supplies its token only to
the push command, and the build reads the exact data commit that was pushed.

## Observation coverage and verification

Every update re-fetches the
[MODAPS S-NPP outage list](https://modaps.modaps.eosdis.nasa.gov/services/production/outages_suomi_npp.html).
A day is observed only when acquired and documented outage intervals cover
strictly less than six UTC hours, using the union of overlapping intervals.
Missing or unobserved target days make every cell label null. Counts from the
available days remain visible, but incomplete weeks are not scored. Successful
empty responses establish acquisition, not proof of fire absence.
The preceding issue's recorded score is withdrawn if refreshed outage coverage
makes that target incomplete.

`verification/track_record.csv` reports AP, Brier score, ROC AUC, recall at 5%
and 10% national area, equal-frequency calibration error, and training
climatology AP on the same week and cells. Undefined metrics are empty CSV
fields with a reason in `notes`. Area recall uses the benchmark's fractional
boundary-cell allocation. Climatology uses cell estimates where available and
department estimates otherwise. Weekly samples are small, cells are dependent,
and a short record cannot establish reliability or a stable improvement.

## Local execution

Set `FIRMS_MAP_KEY` in the environment or repository `.env`; commands never print
the credential. Bootstrap requires the locally prepared benchmark data and
frozen artifacts. Use an orphan `live-data` worktree outside the repository.

```powershell
uv sync --all-extras --frozen
git worktree add --orphan -b live-data ../live-data
uv run --no-sync bolivia-fire live bootstrap --out ../live-data
uv run --no-sync bolivia-fire live update --out ../live-data --issue 2026-10-26
uv run --no-sync bolivia-fire live update --out ../live-data
```

Bootstrap initializes the latest available issue and its preceding issue as
labelled backfills. The default update issue is the latest Monday with an
acquired 365-day input window. `--cache` selects an external response cache;
otherwise it is a sibling directory named `.<checkout-name>-cache`. Keep that
cache for interrupted-bootstrap recovery. Weekly updates need only the data
checkout, source code, Python dependencies, and FIRMS credential.

Repository maintainers must publish the initialized data branch, configure the
`FIRMS_MAP_KEY` repository secret, enable GitHub Pages with Actions, and permit
the `github-pages` deployment environment. The workflow fails when its secret
or initialized state is missing. It uses `npm run build:static` with
`LIVE_DATA_DIR` and `PAGES_BASE=/` for `https://bolivia-fire.cupesi.org/`.
No remote publication is performed
by the local commands. Publication rejects outputs at or above 200 MB; older
archives then need an explicit retention decision.

## Static geography and provenance refresh

After building the [place metadata](dataset.md#place-metadata), refresh an
initialized live-data checkout without acquiring FIRMS observations or running
inference:

```powershell
uv run --no-sync python -m bolivia_fire.live.static --out ../live-data
```

This static update reads existing grid order, writes `static/cell_places.json`,
exports `static/boundaries_adm0.json` through `static/boundaries_adm3.json`, and
completes `static/meta.json`. It does not write forecast, observation, state,
model or verification files. Each display boundary is bounded to 1,500,000 JSON
bytes, simplified with topology preservation, and includes a `simplification`
object containing `tolerance_deg`, `preserve_topology` and `source_sha256`.
Unmodified source geometry is used for cell assignments.

The place JSON is columnar, aligned exactly to `static/grid.json` cell IDs.
Its `format` is `dictionary-v1`: `strings` contains unique strings shared by
`department`, `province`, `municipality`, `nearest_place_name` and
`nearest_place_feature_code`. Those five columns contain zero-based dictionary
indices or null; an empty string remains distinct from null. All other columns
retain their original values. Decoding restores [the schema](../datasets/schemas/cell_places.yaml)
without rounding numbers or changing accents. The static client also accepts
earlier flat exports; the API continues to read the original Parquet columns.
`populated_places[i]` is an array of up to three objects with `name`,
`geonames_id`, `feature_code` and `population`.

One earlier `live-data` commit retains the previous 5,853,252-byte (5.85 MB)
`static/cell_places.json`. History is not rewritten. The normalized export
reduces the current file below 5 MB going forward; it does not remove that
historical blob.

Metadata retains live acquisition provenance and supplies `protocol`
(`version`, `sha256`), `experiments_sha256`, `cells_sha256`, `sources_sha256`,
`cell_places_sha256`, `model_id`, `model_name`, `source_model_sha256`,
`source_calibrator_sha256` and `primary_opening_utc`. Its `dataset_snapshot_ids`
contains the hashes plus `panel`, mapping evaluation panel years to the hashes
recorded at the primary opening, and `evaluation_sources_sha256` for the
original evaluation source registry. `sources_sha256` identifies the expanded
display source registry; it is intentionally distinct from that frozen hash.
The model and calibrator hashes are checked against the primary opening.

`source_attributions` and `attribution` include FIRMS, all four pinned
geoBoundaries levels and GeoNames. CHIRPS is absent from strict live metadata
because the strict model does not use rainfall covariates. The API also credits
CHIRPS for its retrospective layers. No placeholder provenance values are
exported. `boundary_simplification_tolerance_deg` maps each ADM level to the
actual display tolerance in degrees.

## Reference snapshot

The snapshot initialized on October 31, 2026, occupies 14.4 MB, excluding Git
metadata and the external raw-response cache. Its acquisition bootstrap used
61 area requests for 208 additional SP days and 91 closed NRT days, reserving
598 data transaction units.

| Operation | Wall time (seconds) |
|---|---:|
| Acquisition bootstrap | 77.190 |
| Regeneration from cached responses | 49.149 |
| October 26 issue update | 2.140 |

The mask contains 23,359 unique locations. It would remove 8,904 of 6,742,418
nominal/high SP type-0 rows in the bounding box: 0.132%. The retained state
contains 23 unobserved days, including five NRT dates. October 19's target week
is complete; October 26's target week is incomplete in this snapshot.

The October 19 backfill contains 1,713 positive cells out of 37,146. Backfill
scores are published separately in `verification/track_record.csv`; this snapshot
contains no prospective performance evidence.
