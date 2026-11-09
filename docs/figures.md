# Figures and browser media

Documentation uses tracked GIFs and optimized browser PNGs in `docs/assets/`. Scientific figures read recorded artifacts. Browser images show archived weekly estimates separately from historical benchmark estimates.

## Notebooks

```powershell
uv run --no-sync jupyter nbconvert --to notebook --execute --inplace notebooks/01_exploration.ipynb --ExecutePreprocessor.timeout=240
uv run --no-sync jupyter nbconvert --to notebook --execute --inplace notebooks/02_features.ipynb --ExecutePreprocessor.timeout=240
uv run --no-sync jupyter nbconvert --to notebook --execute --inplace notebooks/03_modeling.ipynb --ExecutePreprocessor.timeout=240
uv run --no-sync jupyter nbconvert --to notebook --execute --inplace notebooks/04_evaluation.ipynb --ExecutePreprocessor.timeout=240
```

| Notebook | Contents and inputs |
|---|---|
| [01_exploration](../notebooks/01_exploration.ipynb) | Observation days, outages, seasonal cycle, training frequency map, partitions and label prevalence from the recorded dataset summary and existing figures. |
| [02_features](../notebooks/02_features.ipynb) | Real Santa Cruz cell `r1445c2432` at issue 2024-09-02; pipeline reconstruction, strict definitions, exact no-look-ahead check, LOYO climatology, sampled training distributions and recorded CHIRPS completeness. |
| [03_modeling](../notebooks/03_modeling.ipynb) | Recorded 2021/2022 scores, 20 LightGBM configurations, calibration reliability and saved-model gain importance; strict and strict_geo feature definitions. |
| [04_evaluation](../notebooks/04_evaluation.ipynb) | Recorded temporal/geographic results, H1/H2, Santa Cruz, calibration, area budgets, sensitivity reopenings and optional post-hoc alternative-learner artifacts. |


Notebook execution reads local artifacts, without fitting models, creating new predictions or opening the final test. The evaluation notebook displays the published post-hoc learner comparison when all seven required comparison JSON files are present; otherwise it skips that entire section and lists the missing files. Each notebook can run from a clean kernel. Feature and modeling plots use the shared serif chart style.

## Scientific media

```powershell
uv run --no-sync python -m bolivia_fire.animations --charts-only
```

The chart renderer uses a 512 MB DuckDB limit and two threads. Scientific GIFs have 73 frames, eased transitions at 20 fps and a 2.5-second final hold. Static figures retain 300 dpi metadata. The serif font preference is Times New Roman, Liberation Serif, TeX Gyre Termes, then DejaVu Serif, with STIX math text. The source hashes, dimensions and per-file sizes are in [media.json](assets/media.json).

Mermaid 11.17.2 is pinned in frontend development dependencies. The [local renderer](assets/diagrams/render.mjs) reads the installed bundle and [diagram sources](assets/diagrams/src/).

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
| Historical browser views | Prepared panel and strict prediction export: issue 2024-09-02; September 2, 9, 16 and 23 navigation |
| Archived browser views | Retired live-data archive; 14 issues from 2026-08-03 through 2026-11-02 and 11 scored target weeks |

Outage shading merges only UTC days with at least six documented outage hours: 64 acquisition days, including 56 in issue years 2013–2025. Short overlaps are not shaded. Counts retain partial observations; null labels and unavailable estimates remain distinct from zero. Map counts use log1p and probabilities use a fixed 0–1 scale. Strict covers non-held-out cells; strict_geo covers held-out cells. Crossfade colors are transitions between recorded weeks, not additional estimates. H2 retains the interval without inventing a point estimate. Numeric result tables remain Markdown in [results](results.md).


## Browser captures

Use an API backend with prepared real panel and prediction artifacts at port 8765, an API frontend at port 5173, and a static preview containing real live data at port 4173. The [frontend guide](frontend.md) gives both build modes. Start the API frontend with:

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

From another frontend shell:

```powershell
$env:BASE_URL = 'http://127.0.0.1:5173'
$env:DOCS_STATIC_URL = 'http://127.0.0.1:4173'
$env:CAPTURE_DOCS_MEDIA = '1'
npm run test:e2e -- e2e/screenshots.spec.ts --project=chromium --output=test-results/docs-media
```

Then, from the repository root:

```powershell
uv run --no-sync python scripts/optimize_screenshots.py --frames frontend/test-results/docs-media
```

Ordinary browser tests skip these captures. Readiness checks wait for network idle, loaded fonts, a populated map canvas, a selected cell and its rendered history path, and the absence of loading text. Desktop images use 1,440 × 900; mobile uses 390 × 844. PNGs use an indexed palette and remain below 400,000 bytes.

The navigation GIF shows four complete weekly states without interpolated probabilities. It has 61 frames at 20 fps, a 2,500 ms final hold and a clear 18-pixel border. Its total duration is 5,500 ms; it remains below 3,000,000 bytes. The [media inventory](media.md) lists every image's content, source, language, dimensions and size.

## Summary figure

The summary figure ([PNG](../artifacts/figures/summary_recall10.png), [SVG](../artifacts/figures/summary_recall10.svg)) shows pooled recall at a weekly budget of 10% of eligible area: 39.7% for the original climatology baseline and 55.8% for calibrated strict LightGBM. These are the primary 2023–2025 temporal test results in arXiv:2611.08539v1, Table 3, read from `artifacts/evaluation/test_strict.json`. The context line gives the comparison's 3,127,636 cell-weeks; a separate caption line gives the full dataset's 25,184,988 cell-weeks in 2013–2025. The [source record](../artifacts/figures/summary_recall10.json) contains the metric paths, artifact and PDF hashes, method definitions, alt text, both population counts and their sources, and derivation of 106 eligible weeks out of 157 test weeks.

The exports use a 1700 × 2200 px Letter page at 200 dpi, with one-inch margins and text of at least 48 px (11.3 px at 400 px display width). The renderer requires regular and italic faces from one of these families: TeX Gyre Termes, Times New Roman, Liberation Serif or Nimbus Roman. SVG text is stored as paths. Short figure notes are recorded separately from the full method definitions.

```powershell
uv run --no-sync python -m bolivia_fire.summary_figure
```
