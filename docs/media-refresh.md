# Interface media and documentation verification

The historical estimate layer joins available `strict_geo` predictions on held-out
cells, outlines their blocks, distinguishes unavailable geographic estimates from
ordinary missing data, and identifies the value model in cell detail, history and CSV.
The EN/ES controls use spacing without a text separator.

The live screenshots show issue 2026-10-26 from the snapshot updated
2026-10-31T18:16:52 UTC. Historical screenshots show issue 2024-09-02.
The navigation sequence contains September 2, 9, 16 and 23, 2024.
Desktop screenshots are 1440 × 900; the mobile screenshot is 390 × 844.

## Media inventory

| File | Bytes | Content |
|---|---:|---|
| [live-forecast-desktop.png](assets/screenshots/live-forecast-desktop.png) | 68,858 | National live estimate and selected San José cell history. |
| [live-cell-detail.png](assets/screenshots/live-cell-detail.png) | 68,803 | Zoomed administrative outlines, town labels and selected-cell places. |
| [table-desktop.png](assets/screenshots/table-desktop.png) | 36,620 | Four cells near Campesina el Soto, place columns and selected-cell history. |
| [estimate-mobile.png](assets/screenshots/estimate-mobile.png) | 40,772 | National weekly estimate and probability legend at 390 pixels. |
| [live-spanish-desktop.png](assets/screenshots/live-spanish-desktop.png) | 70,928 | Spanish weekly estimate and selected-cell detail. |
| [estimate-desktop.png](assets/screenshots/estimate-desktop.png) | 79,577 | Historical temporal/geographic estimates, holdout outlines and value-model identity. |
| [observed-desktop.png](assets/screenshots/observed-desktop.png) | 45,112 | Historical observed indicators and complete-label legend. |
| [track-record-desktop.png](assets/screenshots/track-record-desktop.png) | 39,325 | Backfill verification for 2026-10-19 and empty prospective track record. |
| [week_navigation.gif](assets/screenshots/week_navigation.gif) | 327,531 | Four historical estimate weeks; 61 frames, 5.5 seconds and a 2.5-second final hold. |

Each PNG is below 400,000 bytes; the navigation GIF is below 3,000,000 bytes.
The individual screenshots and all four navigation states were visually inspected.

## Validation

| Command | Measured result |
|---|---|
| `npm run check` | Zero errors and warnings. |
| `npm run test` | 57 tests passed. |
| `npm run build` | Passed; initial JavaScript 35,825 bytes gzip, below 37,661 bytes. |
| `npm run build:static` with real live data | Passed; initial JavaScript 35,616 bytes gzip, below 39,338 bytes; data 13,214,724 bytes. |
| `uv run --no-sync pytest tests backend/tests -m "not live and not local_data" -q -p no:cacheprovider` | 458 passed, 6 deselected; 108.32 seconds. |
| Production API Playwright suite | 63 passed across Chromium, Firefox and WebKit. |
| Synthetic static Playwright suite | 63 passed across all three engines. |
| Real live-data static smoke suite | 42 passed across all three engines. |
| Final holdout browser regressions after the geographic-only-week fix | 3 passed, one per engine. |
| Opt-in documentation capture suite, Chromium | Both historical and live static scenarios passed. |
| `uv run --no-sync ruff check` on changed Python modules | Passed. |
| Published animation contract | Passed; 61 frames, clear border and size limit retained. |

The Python suite reports existing Starlette/httpx and DuckDB/pandas deprecations.
Browser language-asset checks require the production API build because they inspect
emitted asset requests. The final-test access log and frozen protocol remain unchanged.

## Documentation

Both READMEs contain matching sections and command blocks, share local images and
links, and include reciprocal language links. The Spanish version uses the interface's
municipio, provincia, departamento, celda, fecha de emisión and semana objetivo terminology.
The documentation index covers the four current notebooks, live publication and the
post-hoc learner comparison. Obsolete verification diaries and population descriptions
were removed; MapBiomas remains omitted from fitted models.

Command-help checks passed for all 39 documented interfaces, including media optimization. The 61 documented CLI
command lines use 29 distinct help pages and contain no unknown options. Both README
renders have seven sections, six loaded images and no horizontal overflow; their
code blocks match exactly. All 236 local file/image references and seven Markdown fragments resolve,
and UTF-8, mojibake, obsolete-name and absolute-local-path checks pass.

Changed files cover both READMEs, the API store/export, the map and its legend/detail
components, prediction/grid types and normalization, English/Spanish messages, regression
tests, screenshot capture/optimization, the media manifest and documentation pages.
The API smoke check passed with 37,146 cells, 678 weeks and 37,146 exported CSV rows.

## Open issues

None. The changes are ready for integration.
