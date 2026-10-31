# Figures and browser media

Documentation uses tracked GIFs and optimized browser PNGs in `docs/assets/`.
Notebooks retain 300 dpi static PNGs in `artifacts/figures/`. Publication copies
are byte-identical GIFs in the ignored publication directory; current exporters
write no PDFs or publication PNGs.

## Reproduction

These commands use local recorded artifacts without fitting or evaluating models:

```powershell
uv sync --all-extras
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/01_exploration.ipynb --ExecutePreprocessor.timeout=240
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/02_features.ipynb --ExecutePreprocessor.timeout=240
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/03_modeling.ipynb --ExecutePreprocessor.timeout=240
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/04_evaluation.ipynb --ExecutePreprocessor.timeout=240
uv run python -m bolivia_fire.animations --charts-only
```

The four notebooks follow the analysis stages:

| Notebook | Contents and inputs |
|---|---|
| [01_exploration](../notebooks/01_exploration.ipynb) | Observation days, outages, seasonal cycle, training frequency map, partitions and label prevalence from the recorded dataset summary and existing figures. |
| [02_features](../notebooks/02_features.ipynb) | Real Santa Cruz cell `r1445c2432` at issue 2024-09-02; pipeline reconstruction, strict definitions, exact no-look-ahead check, LOYO climatology, sampled training distributions and recorded CHIRPS completeness. |
| [03_modeling](../notebooks/03_modeling.ipynb) | Recorded 2021/2022 scores, 20 LightGBM configurations, calibration reliability and saved-model gain importance; strict and strict_geo feature definitions. |
| [04_evaluation](../notebooks/04_evaluation.ipynb) | Recorded temporal/geographic results, H1/H2, Santa Cruz, calibration, area budgets, sensitivity reopenings and optional post-hoc alternative-learner artifacts. |

Notebook execution reads local artifacts and does not regenerate figures, fit models,
produce predictions or open the final test. Exploration and evaluation display existing
300 dpi PNGs. Feature and modeling plots use `figures.chart_style()` and embed small
inline figures; no additional figure files are written. DuckDB sampling uses a 512 MB
query limit and two threads. Each notebook starts independently from a clean kernel.
The comparison section inventories available `comparison_*.json` records and displays
scalar metrics with their source keys; absent comparison records are reported as pending.

UTC DuckDB aggregation uses 512 MB and two threads. Weekly totals must match the
recorded annual summary. Metrics, reliability bins and intervals come from JSON.
Mermaid **11.17.2** is pinned in frontend development dependencies. The
[local Chromium renderer](assets/diagrams/render.mjs) reads the installed bundle,
with no CDN; [diagram sources](assets/diagrams/src/) are also embedded in Markdown.

Build the frontend, then start the isolated capture server from the repository root:

```powershell
cd frontend
npm ci
npm run build
cd ..
uv run --no-sync uvicorn backend.app.main:app --host 127.0.0.1 --port 8768 --no-access-log
```

In another shell:

```powershell
cd frontend
$env:BASE_URL = 'http://127.0.0.1:8768'
$env:CAPTURE_DOCS_MEDIA = '1'
npx playwright test e2e/screenshots.spec.ts --project=chromium --output=test-results/m1-media
cd ..
uv run python -m bolivia_fire.animations --screenshot-frames frontend/test-results/m1-media
```

Stop the isolated server afterwards. Capture is opt-in to preserve published media
during ordinary E2E runs. Desktop PNGs are 1440 × 900; mobile is 390 × 844.

## Sources

| Media | Recorded source and quantity |
|---|---|
| `national_weekly.gif`, `seasonal_cycle.gif` | Canonical detection bundle resolved by `datasets/interim/detection_bundle.json`: national weekly counts and annual cycles, 2012–2025 |
| `dataset_map.gif` | Processed cells and panel: complete training-label frequency and held-out blocks |
| `fire_season_2024.gif` | `artifacts/predictions/strict/test.parquet`, 2024 panel and pinned boundaries: Mondays July 1–October 28 |
| `temporal_ap.gif`, `geographic_ap.gif`, `reliability.gif` | `artifacts/evaluation/test_strict.json`, copied in `artifacts/figures/results_summary.json`: APs, intervals and reliability bins |
| `per_year_ap.gif`, `area_budget.gif`, `h2_interval.gif` | Recorded result-summary keys `figures.per_year`, `figures.area_budget`, `figures.h2` |
| `pipeline.gif`, `information_products.gif` | Frozen `configs/protocol.yaml` and documented information-availability definitions |
| `partitions.gif` | Frozen partition dates, purged panel issue calendar, cell holdout flags and pinned boundary |
| Browser captures | Real API over prepared panel and strict predictions: issue 2024-09-02 and August 5–October 28 navigation |

Outage shading merges only UTC days with at least six documented outage hours:
64 acquisition days, including 56 in issue years 2013–2025. Short overlaps are
not shaded. Counts retain partial observations; null labels and unavailable
estimates remain distinct from zero. Map counts use log1p and probabilities use
a fixed 0–1 scale. Strict covers non-held-out cells; strict_geo covers held-out
cells. Crossfade colors are transitions between recorded weeks, not additional
estimates. H2 retains the interval without inventing a point estimate. Numeric
result tables remain Markdown in [results](results.md).

Animations use eased transitions at 20 fps, loop forever, and hold the complete
final figure for 2.5 seconds. A shared palette and changed-region encoding keep
each GIF below 4 MB. Headings and footers remain stable during transitions.
A white border provides at least 12 clear pixels on every side. Static PNGs
use tight bounding boxes, explicit padding and 300 dpi metadata.

Every chart text element uses the shared serif style in `figures.py`, including
titles, axes, ticks, legends, annotations and captions. The font preference is
Times New Roman, Liberation Serif, TeX Gyre Termes, then DejaVu Serif; mathtext
uses STIX. Regeneration rejects a resolved font outside this serif list. All
11 PNGs and 10 chart GIFs resolved to **Times New Roman** in this run. PNG metadata
and [media.json](assets/media.json) record the resolved names.
[Per-file font and visual review](../artifacts/figures/font_review.json) includes
the byte-identical GIF copies, 12-pixel border results and layout notes.
The `--charts-only` option preserves Mermaid diagrams and UI captures; hashes
confirmed all 12 diagram and screenshot files were unchanged.

[media.json](assets/media.json) records sizes, frames, timing, dimensions, border
checks and source hashes. The inventory below reports visual inspection of
first, middle and final frames, plus each notebook PNG and browser screenshot.

## Media inventory and visual review

| File in docs/assets | Bytes | Frames | FPS | Duration (s) | Border | Resolved chart font | Visual checks |
|---|---:|---:|---:|---:|---|---|---|
| [charts/area_budget.gif](assets/charts/area_budget.gif) | 387,334 | 73 | 20 | 6.10 | Pass | Times New Roman | Both populations, area units, recall scale and four model labels. |
| [charts/dataset_map.gif](assets/charts/dataset_map.gif) | 1,621,140 | 73 | 20 | 6.10 | Pass | Times New Roman | National extent, block outlines, colorbar units and outside legend. |
| [charts/fire_season_2024.gif](assets/charts/fire_season_2024.gif) | 2,242,133 | 73 | 20 | 6.10 | Pass | Times New Roman | Week labels, fixed scales, partial counts and gray missing estimates. |
| [charts/geographic_ap.gif](assets/charts/geographic_ap.gif) | 74,586 | 73 | 20 | 6.10 | Pass | Times New Roman | Spatial comparison, baseline identity, CIs and unclipped points. |
| [charts/h2_interval.gif](assets/charts/h2_interval.gif) | 118,720 | 73 | 20 | 6.10 | Pass | Times New Roman | Both endpoints, zero reference, caption and no invented point. |
| [charts/national_weekly.gif](assets/charts/national_weekly.gif) | 60,601 | 73 | 20 | 6.10 | Pass | Times New Roman | UTC totals, merged qualifying days, visible lighter all-confidence series and stable title. |
| [charts/per_year_ap.gif](assets/charts/per_year_ap.gif) | 370,797 | 73 | 20 | 6.10 | Pass | Times New Roman | All years, four model series, legend and full caption. |
| [charts/reliability.gif](assets/charts/reliability.gif) | 264,944 | 73 | 20 | 6.10 | Pass | Times New Roman | Origin markers, both scales, outside legend, omitted-bin notice and stable bin-count title. |
| [charts/seasonal_cycle.gif](assets/charts/seasonal_cycle.gif) | 511,164 | 73 | 20 | 6.10 | Pass | Times New Roman | All 14 year labels, line patterns, units and full final cycle. |
| [charts/temporal_ap.gif](assets/charts/temporal_ap.gif) | 67,650 | 73 | 20 | 6.10 | Pass | Times New Roman | Model labels, AP intervals, paired differences and final panel titles. |
| [diagrams/information_products.gif](assets/diagrams/information_products.gif) | 227,133 | 73 | 20 | 6.10 | Pass | Unchanged | Complete card labels, stable dark text and unclaimed operational product. |
| [diagrams/partitions.gif](assets/diagrams/partitions.gif) | 107,918 | 73 | 20 | 6.10 | Pass | Unchanged | Year labels, purge dates, full timeline and held-out block map. |
| [diagrams/pipeline.gif](assets/diagrams/pipeline.gif) | 93,709 | 73 | 20 | 6.10 | Pass | Unchanged | Centered content, 24 px margins, fixed nodes, clear arrows and no progress text. |
| [screenshots/estimate-desktop.png](assets/screenshots/estimate-desktop.png) | 68,468 | 1 | — | — | Viewport capture | Unchanged | Exact model ID, selected cell, full history chart and missing-value legend. |
| [screenshots/estimate-mobile.png](assets/screenshots/estimate-mobile.png) | 41,227 | 1 | — | — | Viewport capture | Unchanged | 390 px width, complete map, model notice and readable legend. |
| [screenshots/observed-desktop.png](assets/screenshots/observed-desktop.png) | 37,892 | 1 | — | — | Viewport capture | Unchanged | Complete national map, issue date, positive/negative legend and real cell count. |
| [screenshots/table-desktop.png](assets/screenshots/table-desktop.png) | 31,386 | 1 | — | — | Viewport capture | Unchanged | Readable headers, sorted values, selected cell and history chart. |
| [screenshots/week_navigation.gif](assets/screenshots/week_navigation.gif) | 2,143,835 | 73 | 20 | 6.10 | Pass | Unchanged | Missing-prediction state, week changes, selected history and final layer. |

The regenerated chart GIFs' frames 0, 36 and 72 were visually inspected.
All serif labels, legends, annotations and captions fit without clipping or text
overlap. Each final frame holds for 2.5 seconds. All static figures below passed the 12-pixel white-border check
and were inspected for complete labels, legends, markers and captions.

| Static notebook PNG | Bytes | Frames | Resolved font | 12 px border | Visual checks |
|---|---:|---:|---|---|---|
| [dataset_map.png](../artifacts/figures/dataset_map.png) | 260,577 | 1 | Times New Roman | Pass | Country extent, block outlines and legend below the axes. |
| [dataset_partitions.png](../artifacts/figures/dataset_partitions.png) | 175,608 | 1 | Times New Roman | Pass | Partition labels, purged dates and geographic inset caption. |
| [dataset_seasonal.png](../artifacts/figures/dataset_seasonal.png) | 233,832 | 1 | Times New Roman | Pass | Four partition curves, calendar units and readable legend. |
| [dataset_weekly.png](../artifacts/figures/dataset_weekly.png) | 185,330 | 1 | Times New Roman | Pass | Narrow merged outage spans, UTC axis and legend below the data. |
| [results_area_budget.png](../artifacts/figures/results_area_budget.png) | 180,994 | 1 | Times New Roman | Pass | Both populations, budget units and recall axis. |
| [results_geographic.png](../artifacts/figures/results_geographic.png) | 171,389 | 1 | Times New Roman | Pass | AP and paired intervals, baseline identity and wrapped caption. |
| [results_h2.png](../artifacts/figures/results_h2.png) | 92,249 | 1 | Times New Roman | Pass | Endpoint annotations, zero line and complete explanatory caption. |
| [results_map.png](../artifacts/figures/results_map.png) | 414,942 | 1 | Times New Roman | Pass | Both national maps, full colorbars, labels and wrapped caption. |
| [results_per_year.png](../artifacts/figures/results_per_year.png) | 174,129 | 1 | Times New Roman | Pass | Annual ticks, all series, legend and full caption. |
| [results_reliability.png](../artifacts/figures/results_reliability.png) | 168,669 | 1 | Times New Roman | Pass | Complete markers at zero, finite zoom segment, notice and outside legend. |
| [results_temporal.png](../artifacts/figures/results_temporal.png) | 170,411 | 1 | Times New Roman | Pass | Both CI panels, zero reference and wrapped caption. |

## Verification

Verified on 2026-10-31 against existing local artifacts:

| Command or check | Result |
|---|---|
| uv sync --all-extras | Passed |
| uv run --no-sync ruff check . | Passed, including all four notebooks |
| uv run --no-sync pytest tests backend/tests -m "not live and not local_data" -q -p no:cacheprovider | 453 passed, 6 deselected; 114.45 seconds |
| All four notebook execution commands above | Passed from clean kernels; 6, 7, 6 and 10 code cells, no error outputs |
| Notebook UTF-8 and local-path checks | Passed for decoded source, prose and text outputs; no absolute local paths |
| Evaluation access-log integrity | Final execution left `final_test_log.json` SHA256 unchanged |
| Optional comparison handling | Empty directory and synthetic null baseline-difference checks passed |
| Notebook visual inspection | 15 figure outputs and rendered tables inspected; complete definitions, readable labels and no clipping |
| Earlier frontend: npm run check | No errors or warnings |
| Earlier frontend: npm run test | 19 passed |
| Earlier frontend: npm run build | Passed; 28,562 bytes gzip JavaScript |
| Earlier host Chromium documentation capture | 1 passed; no page errors |
| Local file links in this page | 39 passed |
| GIF source/copy comparison and border checks | Passed for all 13 chart/diagram copies and all 14 tracked GIFs |

Measured clean-kernel notebook wall times, in seconds:

| Notebook | Seconds |
|---|---:|
| `01_exploration.ipynb` | 3.403 |
| `02_features.ipynb` | 9.207 |
| `03_modeling.ipynb` | 4.512 |
| `04_evaluation.ipynb` | 5.220 |

The Python suite emitted the existing Starlette/httpx and DuckDB/pandas warnings.
Notebook execution emitted the existing Windows Jupyter transport and event-loop
notices. Feature checks matched 37,146 cells and 22 history columns against the
saved panel. Adding 100 synthetic future detections with the original FRP dtype
left every history value exactly unchanged. The training sample contains 48,860
negative and 1,140 positive cell-weeks. A direct scan found zero incomplete
CHIRPS feature rows across all 25,184,988 recorded cell-weeks. All five CHIRPS
feature completeness shares were 1.0 in every recorded issue year, 2013-2025. New inline plots resolved to
Times New Roman. Complete feature definitions and compact result tables were
inspected in locally rendered notebook HTML. Frontend and capture entries retain
the earlier verification; those assets were unchanged by notebook restructuring.
