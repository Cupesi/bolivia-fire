# Weekly detection explorer

The static frontend uses Svelte 5, TypeScript, Vite 8 and Tailwind CSS 4. Node
22.12 or newer is required. The lockfile pins dependencies. All API requests use
same-origin `/api/v1` URLs; there are no tile, map, font or chart services.

```powershell
cd frontend
npm ci
npm run check
npm run test
npm run build
```

The build writes `frontend/dist`. Start or restart FastAPI after that directory
exists; its static mount is configured at startup. See [the API contract](api.md).

```powershell
uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 8765
```

For development, `npm run dev` serves port 5173 and proxies `/api` to
`http://127.0.0.1:8765`. The production page is served by FastAPI at port 8765.
`npm run test:api` checks the real local API, including bounded CSV export and
null labels. Set `API_BASE_URL` to change that script's origin.

## Data and interaction

The client uses the exact flat grid, paginated week catalog, model lists keyed
by variant, and columnar time series documented in `docs/api.md`. It loads up to
700 available issue weeks in pages of 100. Requests are cancelled when filters
change; values from a previous selection are cleared while loading. Missing
predictions, incompatible filters, empty responses and connection failures have
separate text states. Estimates are enabled only when metadata lists prepared
prediction layers and models; their label states that they are retrospective
model estimates, not operational forecasts.
The estimate panel displays the exact model identifier and API variant returned
with the loaded layer. The `strict` export includes both temporal models such as
`strict/lightgbm_08` and geographic models such as `strict_geo/lightgbm_04`;
choose the model for the required population. Gray cells can be outside that
model's population or lack complete input history.

The missing-observation notice uses `/weeks.target_complete`, independently of
the `outage` flag for any documented overlap. Short outages can coexist with
complete labels. The zero-detection low-coverage heuristic has its own notice
and does not establish completeness. About text and source attributions cover
NASA FIRMS, geoBoundaries and CHIRPS; MapBiomas is absent from the acquired data.
Provenance uses curated labels, with shared protocol, cell and source hashes
shown once and year-specific panel identifiers available in an expandable view.

The map raster has one pixel per eligible grid cell. Nearest-neighbor scaling
preserves cell blocks. It uses a north-up geographic projection with square
0.05-degree blocks; these do not represent equal ground area. Simplified ADM0
and ADM1 boundaries from the API are drawn on top. Null or absent values are
gray, complete negatives pale yellow, and detected positives teal. Probabilities
use an ordered viridis-like scale with a 0–1 legend. There is no interpolation
between cells and no conversion of missing labels to zero.

Map controls:

- Wheel or +/− buttons zoom; pointer drag pans.
- Focus the map and use arrow keys to move between eligible cells; Enter or
  Space selects. The first selection uses a central eligible cell.
- Ctrl + arrows pans; +/− zooms; Home resets the extent.
- Alt + left/right changes to the previous/next available issue week.

The selected cell retains its department and national area. The weekly history
has independent inclusive date bounds, limited to 700 weeks. The SVG chart
breaks probability paths at null values and marks missing observations below
the axis. An expandable table exposes the same weekly values as text.

The current-week table is the semantic alternative to the canvas. Top 50, 100,
500 or all cells are ranked by the current value, with missing values last.
Column sorting applies to that subset, with 25 rows per page. Selecting a row
opens the same detail panel. CSV links export the complete selected week through
the API, including provenance comments; table pagination does not limit export.

The layout uses two columns on desktop and stacked panels on narrow screens.
Native controls, visible focus, descriptive labels, a skip link, keyboard map
interaction and live status text provide alternatives to pointer interaction.
The canvas uses an application region for spatial keys, alongside the table.
There is no decorative animation.

## Browser tests

`frontend/e2e/explorer.spec.ts` contains nine scenarios for each of Chromium,
Firefox and WebKit: initial render and console errors, available-week navigation,
estimate availability, mouse/keyboard cell selection and zoom, sortable table
and CSV download, offline recovery, a mobile viewport, observation-coverage
consistency, and project identity/source provenance. They use the real
backend and local artifacts. Only the offline test intercepts API requests,
aborting them rather than returning fixture data. Screenshots, downloads and
failure traces go to `frontend/test-results`.
`frontend/e2e/estimates.spec.ts` adds desktop and mobile scenarios that require
the published strict predictions, check temporal and geographic population
counts, verify model identity and the probability legend, and compare chart
points and table values against the real cell-series response.

Prefer the official image matching the pinned `@playwright/test` version. From
the repository root on Windows:

```powershell
docker version
docker run --rm --ipc=host --memory=3g --cpus=2 `
  --mount 'type=bind,source=D:\Repositories\papers\bolivia-fire,target=/work' `
  --mount 'type=volume,target=/work/frontend/node_modules' `
  --workdir /work/frontend `
  --env BASE_URL=http://host.docker.internal:8765 `
  mcr.microsoft.com/playwright:v1.64.1-noble `
  sh -lc 'npm ci --ignore-scripts && npm run test:e2e'
```

Reuse that image for every run. The anonymous volume separates Linux test
dependencies from Windows `node_modules`; `--rm` removes that volume after the
run. Browsers are supplied by the image.

When Docker's engine is unavailable, the authorized host fallback uses the
default shared `%LOCALAPPDATA%\ms-playwright` cache. From `frontend` in
PowerShell, with the real backend already running:

```powershell
npx playwright install chromium firefox webkit
$env:BASE_URL = 'http://127.0.0.1:8765'
$env:PLAYWRIGHT_JSON_OUTPUT_FILE = 'test-results/f2-host-fixed/results.json'
npx playwright test --output=test-results/f2-host-fixed
```

This fallback installs no system dependencies. The separate output directory
preserves earlier screenshots and reports.

## Verification on 2026-10-31

Observed against the backend serving local artifacts at port 8765:

| Check | Result |
|---|---|
| `npm ci` | Passed |
| `npm run check` | Passed, no errors or warnings |
| `npm run test` | Passed, 19 tests in three files |
| `npm run build` | Passed; JavaScript 27,922 bytes gzip, below 120,000-byte target |
| `npm run test:api` | Passed on real artifacts |
| `npx playwright test --list` | Collected 21 tests across three engines |
| `npx playwright install chromium firefox webkit` | Passed using the shared host cache |
| Host Playwright suite | Passed, 21 tests in 25.7 seconds; no retries, skips or flaky results |

| Host engine | Passed | Failed |
|---|---:|---:|
| Chromium 156.0.8078.4 | 7 | 0 |
| Firefox 157.0 | 7 | 0 |
| WebKit 27.2 | 7 | 0 |

The API smoke check observed 37,146 cells and 678 issue weeks, ending
2025-12-29. The current-week layer and CSV each contained 37,146 rows. The CSV
was 1,643,413 bytes. The 2017-09-25 layer preserved 37,146 null labels. A selected
cell returned 52 weekly records for the checked range. Protocol metadata was
version 1.1. No prepared predictions were listed in the running API.

The initial host run passed 19 tests and failed mouse selection in Chromium and
WebKit. Focusing the map on pointer-down scrolled the page by 84 and 85 pixels,
respectively, before pointer-up, selecting `r1416c2234` instead of `r1462c2234`.
`CellMap.svelte` now focuses with `preventScroll: true`. The unchanged full suite
then passed, and desktop pointer selection preserved scroll position zero in
all three engines. Initial and final reports are
`frontend/test-results/f2-host/results.json` and
`frontend/test-results/f2-host-fixed/results.json`; the initial failure folders
retain screenshots and traces.

Twelve additional screenshots were visually inspected after cell history
finished loading: map/detail and table/detail views at 1440 by 1000 and 390 by
844 in each engine. Map boundaries, selected cells, chart missing-observation
marks, controls and table rows rendered without clipping. Every mobile document
width measured 390 pixels. The six browser/viewport reviews recorded 96
same-origin requests, zero failed requests, zero HTTP errors, zero console
errors and zero page errors. These normal-interaction reviews exclude the
suite's intentional offline aborts; offline recovery passed in every engine.
Diagnostics are in `frontend/test-results/f2-review/browser-diagnostics.json`.

Screenshot paths below are relative to `frontend/test-results/f2-review`:

| Engine | Desktop map/detail | Mobile map/detail | Desktop table/detail | Mobile table/detail |
|---|---|---|---|---|
| Chromium | `chromium-desktop.png` | `chromium-mobile.png` | `chromium-desktop-table.png` | `chromium-mobile-table.png` |
| Firefox | `firefox-desktop.png` | `firefox-mobile.png` | `firefox-desktop-table.png` | `firefox-mobile-table.png` |
| WebKit | `webkit-desktop.png` | `webkit-mobile.png` | `webkit-desktop-table.png` | `webkit-mobile-table.png` |

The shared browser cache measured 4,417,989,594 bytes (4.115 GiB), including
pre-existing versions and browser profiles. The five installed engine/helper
directories total 1,293,586,218 bytes (1.205 GiB): `chromium-1248`,
`chromium_headless_shell-1248`, `firefox-1555`, `webkit-2370` and `ffmpeg-1013`.
The existing backend remained running; no server was started or stopped.

Docker Desktop previously returned HTTP 500 for its Linux engine API. That
engine remains unavailable; Docker was not rerun for host acceptance. Prepared
prediction layers were absent, so the estimate-availability scenario verified
the disabled control and missing-prediction explanation.

`Get-PSDrive -Name D` measured 102.31 GiB free at the start and at least
101.13 GiB at subsequent checkpoints, above the required 55 GB floor.

`npm audit` reported two low-severity development-dependency entries:
`deepmerge` and its dependent `@sveltejs/vite-plugin-svelte`, with no fix offered.

## Coverage and provenance verification

The consistency checks on 2026-10-31 used a separate backend started with
`uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8766 --no-access-log`.
The existing port 8765 process was left running. Host browsers came from the
shared cache; no browser installation or data download was needed.

| Check | Result |
|---|---|
| `npm run check` | Passed; no errors or warnings |
| `npm run test` | 19 passed |
| `npm run build` | Passed; JavaScript 28,294 bytes gzip |
| `uv run pytest -q backend/tests -p no:cacheprovider` | 34 passed |
| `uv run pytest -q tests backend/tests -p no:cacheprovider` | 290 passed; one live test deselected |
| `uv run ruff check backend` | Passed |
| Host Playwright suite | 27 passed in 30.9 seconds; no retries, skips or flaky results |

| Engine | Passed | Failed |
|---|---:|---:|
| Chromium | 9 | 0 |
| Firefox | 9 | 0 |
| WebKit | 9 | 0 |

Both the backend and E2E regressions check 2022-08-01, with 37,146 missing
labels, and 2025-12-29, with 37,146 complete labels. The latter overlaps a
12-minute documented outage on 2025-12-30, which does not make a target day
unobserved. Synthetic backend coverage also checks a complete week with the
low-coverage heuristic set independently.

The initial full run passed 26 tests and failed WebKit's exact mouse-selection
assertion. WebKit truncated the requested page Y coordinate 778.956 to 778;
the 0.956-pixel shift exceeded half the 1.820-pixel cell height. The test now
clicks the nearest CSS pixel to the intended cell center and retains the exact
cell assertion. Initial and final results are in
`frontend/test-results/f3-host/results.json` and
`frontend/test-results/f3-host-final/results.json`.

Twelve final screenshots were visually inspected: complete-week, outage-week,
expanded provenance and mobile views in all three engines. Complete weeks have
no missing-observation notice; the outage week has separate missing-observation
and low-coverage notices. The curated provenance labels, source list and project
name render without clipping. Each engine's scenario folder under
`frontend/test-results/f3-host-final` contains the corresponding PNG files.

The isolated port 8766 backend was stopped after verification.

Pytest emitted the existing upstream Starlette TestClient/httpx deprecation
warning. Playwright emitted the existing `NO_COLOR`/`FORCE_COLOR` environment
warning. Prepared prediction layers were absent, so estimate availability was
verified through the disabled control and its explanation.

## Published estimate verification

Verification on 2026-10-31 used the host browser cache and a separate backend
started on `127.0.0.1:8767`. The three strict prediction splits and their
geographic models were present, with model and calibrator hashes checked against
the single final-test opening. No training, selection or evaluation command was
run during this verification.

| Check | Result |
|---|---|
| `npm run check` | Passed; zero errors or warnings |
| `npm run test` | 19 passed |
| `npm run build` | Passed; JavaScript 28,360 bytes gzip |
| `uv run --no-sync ruff check src tests backend` | Passed |
| `uv run --no-sync pytest -q tests backend/tests -p no:cacheprovider` | 385 passed; one live test deselected; 201.13 seconds |
| Host Playwright suite | 33 passed in 55.8 seconds; zero failures, retries, skips or flaky results |

| Engine | Passed | Failed |
|---|---:|---:|
| Chromium | 11 | 0 |
| Firefox | 11 | 0 |
| WebKit | 11 | 0 |

The host browser command, run from `frontend`, was:

```powershell
$env:BASE_URL='http://127.0.0.1:8767'
$env:PLAYWRIGHT_JSON_OUTPUT_FILE='test-results/p1-host-final/results.json'
npm run test:e2e -- --output=test-results/p1-host-final
```

All six `desktop-estimate-history.png` and `mobile-estimate-history.png` images
under `frontend/test-results/p1-host-final` were visually inspected. The
retrospective notice, exact model ID and variant, 0–1 legend, and probability
line alongside observed indicators were readable in all engines, with no
horizontal overflow at 390 pixels. The tests also checked both selected model
populations on 2024-09-02: 29,506 temporal cells and 7,640 geographic cells.

The new estimate test initially demonstrated the missing exact model identity
in the map panel, then passed after adding it. The complete final browser report
is `frontend/test-results/p1-host-final/results.json`.

Pytest reported 11 warnings: the Starlette/httpx deprecation, eight DuckDB/pandas
deprecations, and two tkinter cleanup warnings emitted during the real-artifact
API test. Playwright reported the `NO_COLOR`/`FORCE_COLOR` environment warning.
The verification server on port 8767 was stopped after all checks.
