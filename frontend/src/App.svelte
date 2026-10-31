<script lang="ts">
  import { onMount } from "svelte";
  import CellMap from "./components/CellMap.svelte";
  import CellTable from "./components/CellTable.svelte";
  import SeriesChart from "./components/SeriesChart.svelte";
  import {
    ApiError,
    exportUrl,
    getJson,
    loadInitial,
    loadLayer,
    normalizeSeries,
  } from "./lib/api";
  import { formatValue } from "./lib/color";
  import type {
    Cell,
    Layer,
    LayerKind,
    NullableNumber,
    SeriesPoint,
  } from "./lib/types";

  let data = $state<Awaited<ReturnType<typeof loadInitial>> | null>(null);
  let bootLoading = $state(true);
  let bootError = $state<Error | null>(null);
  let date = $state("");
  let kind = $state<LayerKind>("observed");
  let model = $state("");
  let variant = $state("");
  let view = $state<"map" | "table">("map");
  let layer = $state<Layer | null>(null);
  let layerLoading = $state(false);
  let layerError = $state<Error | null>(null);
  let retry = $state(0);
  let selected = $state<Cell | null>(null);
  let series = $state<SeriesPoint[]>([]);
  let seriesLoading = $state(false);
  let seriesError = $state<Error | null>(null);
  let rangeStart = $state("");
  let rangeEnd = $state("");
  let initialController: AbortController | null = null;
  const weekIndex = $derived(
    data?.weeks.findIndex((week) => week.date === date) ?? -1,
  );
  const currentWeek = $derived(data?.weeks[weekIndex]);
  const provenance = $derived([
    ["Protocol version", data?.meta.protocolVersion],
    [
      "Protocol SHA-256",
      data?.meta.protocolHash || data?.meta.snapshots.protocol_sha256,
    ],
    ["Cells SHA-256", data?.meta.snapshots.cells_sha256],
    ["Panel snapshot", panelIdentifiers(data?.meta.snapshots ?? {})],
    ["Sources SHA-256", data?.meta.snapshots.sources_sha256],
  ]);

  function panelIdentifiers(snapshots: Record<string, unknown>): string | null {
    const panel = snapshots.panel;
    if (!panel || typeof panel !== "object" || Array.isArray(panel))
      return null;
    const labels: Record<string, string> = {
      protocol_sha256: "Protocol SHA-256",
      sources_sha256: "Sources SHA-256",
      cells_sha256: "Cells SHA-256",
      firms_manifest_sha256: "FIRMS manifest SHA-256",
      outage_snapshot_sha256: "Outage snapshot SHA-256",
      detections_sha256: "Detections SHA-256",
      acquisition_days_sha256: "Acquisition days SHA-256",
    };
    const years = Object.entries(panel).flatMap(([year, identifiers]) => {
      if (
        !identifiers ||
        typeof identifiers !== "object" ||
        Array.isArray(identifiers)
      )
        return [];
      const entries = Object.entries(identifiers).flatMap(([key, value]) =>
        labels[key] && typeof value === "string" && value !== snapshots[key]
          ? [[labels[key], value]]
          : [],
      );
      return entries.length ? [[year, Object.fromEntries(entries)]] : [];
    });
    return years.length
      ? JSON.stringify(Object.fromEntries(years), null, 2)
      : null;
  }
  const availableModels = $derived(data?.meta.models[variant] ?? []);
  const estimatesAvailable = $derived(
    Boolean(
      data?.meta.layers.includes("estimate") &&
      Object.values(data.meta.models).some((models) => models.length > 0),
    ),
  );
  const values = $derived(
    new Map<number, NullableNumber>(
      layer?.cell_index.map((index, offset) => [index, layer!.value[offset]]) ??
        [],
    ),
  );
  const summary = $derived.by(() => {
    if (!layer) return "";
    const complete = layer.value.filter((value) => value !== null).length;
    return `Showing ${layer.value.length.toLocaleString("en")} cells · ${complete.toLocaleString("en")} with data · ${(layer.value.length - complete).toLocaleString("en")} missing`;
  });

  async function boot() {
    initialController?.abort();
    const controller = new AbortController();
    initialController = controller;
    bootLoading = true;
    bootError = null;
    try {
      const result = await loadInitial(controller.signal);
      if (controller.signal.aborted) return;
      data = result;
      date = result.weeks.at(-1)?.date ?? "";
      variant = result.meta.variants.includes("strict")
        ? "strict"
        : (result.meta.variants[0] ?? "");
      model = result.meta.models[variant]?.[0] ?? "";
      rangeEnd = date;
      const oneYearBefore = date
        ? new Date(Date.parse(date + "T00:00:00Z") - 364 * 86400000)
            .toISOString()
            .slice(0, 10)
        : "";
      rangeStart =
        result.weeks.find((week) => week.date >= oneYearBefore)?.date ?? date;
    } catch (error) {
      if (!controller.signal.aborted) bootError = asError(error);
    } finally {
      if (!controller.signal.aborted) bootLoading = false;
    }
  }
  function asError(error: unknown): Error {
    return error instanceof Error
      ? error
      : new Error("The request could not be completed.");
  }
  onMount(() => {
    void boot();
    return () => initialController?.abort();
  });

  $effect(() => {
    if (!availableModels.includes(model)) model = availableModels[0] ?? "";
  });

  $effect(() => {
    const initial = data;
    const selectedDate = date;
    const selectedKind = kind;
    const selectedModel = model;
    const selectedVariant = variant;
    void retry;
    if (!initial) return;
    layer = null;
    layerError = null;
    if (
      !selectedDate ||
      !initial.weeks.some((week) => week.date === selectedDate)
    ) {
      layerLoading = false;
      if (initial.weeks.length)
        layerError = new ApiError(
          "filters",
          422,
          "Incompatible filters. Choose one of the available issue weeks.",
        );
      return;
    }
    const controller = new AbortController();
    layerLoading = true;
    loadLayer(
      selectedDate,
      selectedKind,
      selectedModel,
      selectedVariant,
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) layer = result;
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) layerError = asError(error);
      })
      .finally(() => {
        if (!controller.signal.aborted) layerLoading = false;
      });
    return () => controller.abort();
  });

  $effect(() => {
    const cell = selected;
    const start = rangeStart;
    const end = rangeEnd;
    const selectedModel = model;
    const selectedVariant = variant;
    series = [];
    seriesError = null;
    seriesLoading = false;
    if (!cell || !start || !end) return;
    if (
      start > end ||
      (Date.parse(end) - Date.parse(start)) / 86400000 > 4893
    ) {
      seriesError = new ApiError(
        "filters",
        422,
        "Incompatible range. Use a start before the end and a maximum of 700 weeks.",
      );
      return;
    }
    const controller = new AbortController();
    const query = new URLSearchParams({ start, end });
    if (selectedModel) query.set("model", selectedModel);
    if (selectedVariant) query.set("variant", selectedVariant);
    seriesLoading = true;
    getJson(
      `/cells/${encodeURIComponent(cell.id)}/series?${query}`,
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) series = normalizeSeries(result);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) seriesError = asError(error);
      })
      .finally(() => {
        if (!controller.signal.aborted) seriesLoading = false;
      });
    return () => controller.abort();
  });

  function changeWeek(direction: number) {
    if (!data) return;
    const target = data.weeks[weekIndex + direction];
    if (target) date = target.date;
  }
  function weekKeyboard(event: KeyboardEvent) {
    if (event.altKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault();
      changeWeek(event.key === "ArrowLeft" ? -1 : 1);
    }
  }
  function safeLink(url: string): boolean {
    return /^https:\/\//.test(url);
  }
</script>

<svelte:window onkeydown={weekKeyboard} />

<a class="skip-link" href="#explorer">Skip to data explorer</a>
<header class="page-header">
  <div class="header-inner">
    <a class="project-name" href="/">Bolivia Fire</a><a href="#about"
      >About & limitations</a
    >
  </div>
</header>
<main>
  <div class="page-intro">
    <p class="eyebrow">Satellite observations · Weekly · 0.05°</p>
    <h1>Weekly fire detections</h1>
    <p class="intro-copy">
      Explore observed satellite detections and prepared retrospective estimates
      across Bolivia.
    </p>
  </div>

  {#if bootLoading}
    <div class="state-panel" role="status">
      <h2>Loading the data explorer</h2>
      <p>Reading the grid, available weeks and dataset metadata.</p>
    </div>
  {:else if bootError}
    <div class="state-panel error" role="alert">
      <h2>
        {bootError instanceof ApiError && bootError.kind === "offline"
          ? "API offline"
          : "Data service unavailable"}
      </h2>
      <p>{bootError.message}</p>
      <button class="primary" onclick={() => void boot()}
        >Retry connection</button
      >
    </div>
  {:else if data}
    <section id="explorer" aria-label="Data explorer">
      <div class="filter-bar">
        <div class="week-control">
          <label for="issue-week"
            >Issue week <span class="muted small">Monday · UTC</span></label
          >
          <div class="week-input">
            <button
              aria-label="Previous week"
              disabled={weekIndex <= 0}
              onclick={() => changeWeek(-1)}>←</button
            ><input
              id="issue-week"
              type="date"
              bind:value={date}
              min={data.weeks[0]?.date}
              max={data.weeks.at(-1)?.date}
              aria-describedby="week-hint"
              disabled={!data.weeks.length}
            /><button
              aria-label="Next week"
              disabled={weekIndex < 0 || weekIndex >= data.weeks.length - 1}
              onclick={() => changeWeek(1)}>→</button
            >
          </div>
        </div>
        <fieldset class="layer-control">
          <legend>Layer</legend>
          <div class="segmented">
            <button
              aria-pressed={kind === "observed"}
              onclick={() => {
                kind = "observed";
              }}>Observed detections</button
            ><button
              aria-pressed={kind === "estimate"}
              disabled={!estimatesAvailable}
              aria-describedby={!estimatesAvailable
                ? "missing-predictions"
                : undefined}
              onclick={() => {
                kind = "estimate";
              }}>Retrospective estimate</button
            >
          </div>
        </fieldset>
        <label for="model"
          >Model<select
            id="model"
            bind:value={model}
            disabled={!availableModels.length}
            ><option value="" disabled>None available</option
            >{#each availableModels as option}<option value={option}
                >{option.replaceAll("_", " ")}</option
              >{/each}</select
          ></label
        >
        <label for="variant"
          >Variant<select
            id="variant"
            bind:value={variant}
            disabled={!data.meta.variants.length}
            ><option value="" disabled>None available</option
            >{#each data.meta.variants as option}<option value={option}
                >{option.replaceAll("_", " ")}</option
              >{/each}</select
          ></label
        >
      </div>
      <div class="filter-notes">
        <p id="week-hint" class="muted small">
          Alt + ← / → changes the issue week. Targets cover the following seven
          days.
        </p>
        {#if !estimatesAvailable}<p
            id="missing-predictions"
            class="muted small"
          >
            Missing predictions: no prepared estimates are available.
          </p>{/if}
      </div>
      {#if kind === "estimate"}<p class="notice">
          Retrospective model estimate, not an operational forecast.
        </p>{/if}
      {#if currentWeek?.targetComplete === false}<p
          class="notice"
          data-testid="observation-notice"
        >
          This week includes unobserved target days. Incomplete labels are
          missing, never zero.
        </p>{/if}
      {#if currentWeek?.lowCoverage}<p
          class="notice"
          data-testid="low-coverage-notice"
        >
          Low-coverage heuristic: at least one target day has zero detections in
          the acquisition area. This flag does not determine label completeness.
        </p>{/if}
      <div class="workspace">
        <section class="map-panel" aria-label="Weekly grid">
          <div class="panel-toolbar">
            <div>
              <h2>
                {kind === "observed"
                  ? "Observed detections"
                  : "Retrospective estimate"}
              </h2>
              <p class="muted small">
                {date ||
                  "No available issue weeks"}{#if currentWeek?.completeness != null}
                  {" · "}{(currentWeek.completeness * 100).toFixed(1)}% label
                  completeness{/if}
              </p>
              {#if kind === "estimate" && layer?.model}
                <p class="muted small">
                  Model: {layer.model} · Variant: {layer.variant}
                </p>
              {/if}
            </div>
            <div class="segmented view-toggle">
              <button
                aria-label="Map view"
                aria-pressed={view === "map"}
                onclick={() => {
                  view = "map";
                }}>Map</button
              ><button
                aria-label="Table view"
                aria-pressed={view === "table"}
                onclick={() => {
                  view = "table";
                }}>Table</button
              >
            </div>
          </div>
          <p class="data-status" data-testid="layer-status" role="status">
            {layerLoading
              ? "Loading weekly values…"
              : layerError
                ? layerError instanceof ApiError &&
                  layerError.kind === "missing" &&
                  kind === "estimate"
                  ? "Missing predictions for this week, model and variant."
                  : layerError.message
                : summary ||
                  "No cells or issue weeks are available in this data snapshot."}
          </p>
          {#if layerLoading}<div class="map-state" aria-busy="true">
              <p>Loading weekly values…</p>
            </div>
          {:else if layerError}<div class="map-state error" role="alert">
              <h3>
                {layerError instanceof ApiError &&
                layerError.kind === "missing" &&
                kind === "estimate"
                  ? "Missing predictions"
                  : layerError instanceof ApiError &&
                      layerError.kind === "offline"
                    ? "API offline"
                    : "Selection unavailable"}
              </h3>
              <p>{layerError.message}</p>
              <button
                onclick={() => {
                  retry++;
                }}>Retry weekly data</button
              >
            </div>
          {:else if !data.grid.cells.length || !layer?.cell_index.length}<div
              class="map-state"
            >
              <h3>No weekly data</h3>
              <p>
                No cells or issue weeks are available in this data snapshot.
              </p>
            </div>
          {:else if view === "map"}<CellMap
              grid={data.grid}
              boundaries={data.boundaries}
              {values}
              {kind}
              {selected}
              onselect={(cell) => {
                selected = cell;
              }}
            />
          {:else}<CellTable
              grid={data.grid}
              {values}
              {kind}
              onselect={(cell) => {
                selected = cell;
              }}
            />{/if}
          <div class="legend" aria-label="Map legend">
            {#if kind === "estimate"}<div class="probability-legend">
                <span>Probability</span>
                <div class="color-scale"></div>
                <div class="scale-labels">
                  <span>0</span><span>0.5</span><span>1</span>
                </div>
              </div>
            {:else}<span><i class="swatch detected"></i>Detected</span><span
                ><i class="swatch negative"></i>Not detected</span
              >{/if}
            <span
              ><i class="swatch missing"></i>Missing observation{kind ===
              "estimate"
                ? " / estimate"
                : ""}</span
            >
          </div>
          <div class="panel-footer">
            <p class="small muted">
              Gray means unavailable data. A missing label does not establish
              absence.
            </p>
            {#if layer && !layerLoading && !layerError}<a
                class="download-link"
                href={exportUrl(date, kind, model, variant)}
                download>Download week CSV</a
              >{/if}
          </div>
        </section>
        <aside class="detail-panel" aria-label="Cell detail">
          <div class="detail-heading">
            <h2>Cell detail</h2>
            <span class="muted small">National intersection</span>
          </div>
          {#if selected}
            <h3 class="cell-id" data-testid="selected-cell">{selected.id}</h3>
            <dl class="cell-facts">
              <div>
                <dt>Department</dt>
                <dd>{selected.department || "Unassigned"}</dd>
              </div>
              <div>
                <dt>National area</dt>
                <dd>{selected.area.toFixed(2)} km²</dd>
              </div>
              <div>
                <dt>
                  Current {kind === "estimate" ? "probability" : "observation"}
                </dt>
                <dd>
                  {layerLoading
                    ? "Loading…"
                    : layerError
                      ? "Unavailable"
                      : formatValue(values.get(selected.index), kind)}
                </dd>
              </div>
            </dl>
            <div class="series-heading">
              <h3>Weekly history</h3>
              <p class="small muted">
                p = estimated probability · y = observed indicator
              </p>
            </div>
            <div class="range-controls">
              <label for="range-start"
                >From<input
                  id="range-start"
                  type="date"
                  bind:value={rangeStart}
                  min={data.meta.start || undefined}
                  max={rangeEnd || undefined}
                /></label
              ><label for="range-end"
                >To<input
                  id="range-end"
                  type="date"
                  bind:value={rangeEnd}
                  min={rangeStart || undefined}
                  max={data.meta.end || undefined}
                /></label
              >
            </div>
            {#if seriesLoading}<p class="muted" role="status">
                Loading cell history…
              </p>{:else if seriesError}<p class="error-text" role="alert">
                {seriesError.message}
              </p>{:else}<SeriesChart points={series} />{/if}
            <p class="small muted series-note">
              Observed y is 1 when at least one selected detection occurred, 0
              when none occurred in a complete interval. Gaps stay missing.
            </p>
          {:else}<div class="selection-empty">
              <h3>Select a grid cell</h3>
              <p>
                Click the map or choose a cell in the table to inspect its area
                and weekly history.
              </p>
              <p class="small muted">
                Keyboard: focus the map, move with arrow keys and press Enter.
              </p>
            </div>{/if}
        </aside>
      </div>
    </section>
    <section id="about" class="about-panel" aria-label="About and limitations">
      <details open>
        <summary>About & limitations</summary>
        <div class="about-grid">
          <div>
            <h3>What is a detection?</h3>
            <p>
              The target is at least one VIIRS S-NPP standard-processing
              detection of presumed vegetation fire, with nominal or high
              confidence, in the national part of a cell during the target week.
            </p>
            <p>
              Detections are not fires, ignitions, burned area, or official
              warnings. A zero is not proof of absence: clouds, smoke, satellite
              overpass timing and small fires affect detection.
            </p>
            <h3>Observation coverage</h3>
            <p>
              A day must be acquired and have less than six hours of documented
              sensor outages to be observed. Any unobserved day makes the weekly
              label missing. Observed counts can remain available during
              incomplete weeks.
            </p>
          </div>
          <div>
            <h3>Retrospective estimates</h3>
            <p>
              These are retrospective model estimates, not operational
              forecasts. The strict simulation assumes standard-processing
              detections approximate those available in near real time; that
              equivalence is not validated. Revised CHIRPS rainfall covariates
              do not recreate historical information availability.
            </p>
            {#if data.meta.limitations.length}<details class="api-limitations">
                <summary>Dataset-specific limitations</summary
                >{#each data.meta.limitations as limitation}<p>
                    {limitation}
                  </p>{/each}
              </details>{/if}
            <h3>Sources</h3>
            {#each data.meta.attributions as source}<p class="small">
                {#if safeLink(source.url)}<a href={source.url}>{source.name}</a
                  >{:else}{source.name}{/if}
                {#if source.product} · {source.product}{/if}
                {#if source.provider} · {source.provider}{/if}
                {#if source.license}
                  · {#if safeLink(source.licenseUrl)}<a href={source.licenseUrl}
                      >{source.license}</a
                    >{:else}{source.license}{/if}
                {/if}
                {#if source.processing}<br />{source.processing}{/if}
                {#if source.citation}<br />{source.citation}{/if}
              </p>{:else}<p>
                Source credits are unavailable. See the
                <a href="https://github.com/Cupesi/bolivia-fire/blob/main/datasets/dataset_card.md"
                  >dataset source terms</a
                >.
              </p>{/each}
          </div>
        </div>
        <div class="provenance">
          <h3>Data provenance</h3>
          <dl>
            {#each provenance as [name, value]}<div>
                <dt>{name}</dt>
                <dd class="hash">
                  {#if name === "Panel snapshot" && typeof value === "string"}<details
                    >
                      <summary>View identifiers</summary>
                      <pre>{value}</pre>
                    </details>{:else}{typeof value === "string" && value
                      ? value
                      : "Not supplied by API"}{/if}
                </dd>
              </div>{/each}
          </dl>
          {#if !Object.keys(data.meta.snapshots).length}<p class="muted small">
              No dataset snapshot identifiers were supplied by the API.
            </p>{/if}
          <p class="small muted">
            Grid cells use geographic coordinates and vary in area. Area values
            measure each cell’s national intersection on WGS84.
          </p>
        </div>
      </details>
    </section>
  {/if}
  <footer class="page-footer">
    Bolivia weekly detection benchmark · Research data explorer
  </footer>
</main>
