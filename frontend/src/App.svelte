<script lang="ts">
  import {
    i18n,
    message,
    countMessage,
    formatNumber,
    formatPercent,
    formatUnit,
    formatDate,
    formatTime,
    formatWindow,
    localizeText,
    errorText,
  } from "./lib/i18n";
  import { initialLanguage, selectLanguage, type Language } from "./lib/i18n";
  import { en } from "./lib/messages/en";
  import { onMount } from "svelte";
  import CellPlaces from "./components/CellPlaces.svelte";
  import {
    loadInitial,
    loadLayer,
    loadSeries,
    liveClient,
    staticMode,
  } from "./lib/data-client";
  import { ApiError } from "./lib/api";
  import { weekCsv } from "./lib/cell-table";
  import { formatValue } from "./lib/color";
  import type {
    Cell,
    Layer,
    LayerKind,
    NullableNumber,
    SeriesPoint,
    Observation,
    Forecast,
  } from "./lib/types";

  let data = $state<Awaited<ReturnType<typeof loadInitial>> | null>(null);
  let bootLoading = $state(true);
  let bootError = $state<Error | null>(null);
  let date = $state("");
  let kind = $state<LayerKind>(staticMode ? "estimate" : "observed");
  let observation = $state<Observation | null>(null);
  let observationError = $state<Error | null>(null);
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
  let csvUrl = $state("");
  let languageError = $state(false);
  async function chooseLanguage(language: Language, persist = true) {
    languageError = false;
    try {
      await selectLanguage(language, persist);
    } catch {
      languageError = true;
    }
  }
  const weekIndex = $derived(
    data?.weeks.findIndex((week) => week.date === date) ?? -1,
  );
  const currentWeek = $derived(data?.weeks[weekIndex]);
  const showHoldout = $derived(!staticMode && kind === "estimate");
  const holdoutCells = $derived(
    showHoldout
      ? (data?.grid.cells.filter((cell) => cell.geoHoldout) ?? [])
      : [],
  );

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
  const holdoutAvailable = $derived(
    holdoutCells.some((cell) => values.get(cell.index) != null),
  );
  const holdoutUnavailable = $derived(
    holdoutCells.some((cell) => values.get(cell.index) == null),
  );
  const selectedValueModel = $derived(
    selected && layer
      ? (layer.value_model?.[layer.cell_index.indexOf(selected.index)] ??
          (values.get(selected.index) != null ? layer.model : null))
      : null,
  );
  const summary = $derived.by(() => {
    if (!layer) return "";
    const complete = layer.value.filter((value) => value !== null).length;
    return countMessage($i18n, "layerSummary", layer.value.length, {
      complete: formatNumber(complete, $i18n.language),
      missing: formatNumber(layer.value.length - complete, $i18n.language),
    });
  });
  const counts = $derived(
    new Map<number, NullableNumber>(
      layer?.cell_index.map((index, offset) => [
        index,
        layer!.n_primary?.[offset] ?? null,
      ]) ?? [],
    ),
  );
  $effect(() => {
    csvUrl = "";
    if (!data || !layer) return;
    const url = URL.createObjectURL(
      new Blob([weekCsv(data.grid.cells, layer, date, kind, data.meta)], {
        type: "text/csv;charset=utf-8",
      }),
    );
    csvUrl = url;
    return () => URL.revokeObjectURL(url);
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
      date = result.live?.latest_issue ?? result.weeks.at(-1)?.date ?? "";
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
      : new Error(en.theRequestCouldNotBeCompleted);
  }
  onMount(() => {
    void chooseLanguage(initialLanguage(), false);
    void boot();
    return () => initialController?.abort();
  });

  $effect(() => {
    if (!availableModels.includes(model)) model = availableModels[0] ?? "";
  });

  $effect(() => {
    const selectedDate = date;
    const week = currentWeek;
    observation = null;
    observationError = null;
    if (!staticMode || !week?.observation) return;
    const controller = new AbortController();
    liveClient
      .loadLayer(selectedDate, "observed", "", "", controller.signal)
      .then((result) => {
        if (!controller.signal.aborted)
          observation = result.observation ?? null;
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) observationError = asError(error);
      });
    return () => controller.abort();
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
        layerError = new ApiError("filters", 422, en.invalidWeek);
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
      seriesError = new ApiError("filters", 422, en.invalidRange);
      return;
    }
    const controller = new AbortController();
    seriesLoading = true;
    loadSeries(
      cell,
      start,
      end,
      selectedModel,
      selectedVariant,
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) series = result;
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
    if (target) {
      date = target.date;
      if (staticMode) kind = "estimate";
    }
  }
  function weekKeyboard(event: KeyboardEvent) {
    if (event.altKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault();
      changeWeek(event.key === "ArrowLeft" ? -1 : 1);
    }
  }
</script>

<svelte:window onkeydown={weekKeyboard} />

{#snippet forecastModel(forecast: Forecast)}
  {forecast.variant} / {forecast.model_name ?? $i18n.messages.model}
  (<span title={forecast.model_id}>{forecast.model_id.slice(0, 8)}</span>)
{/snippet}

<a class="skip-link" href="#explorer">{$i18n.messages.skipToDataExplorer}</a>
<header class="page-header">
  <div class="header-inner">
    <a class="project-name" href={import.meta.env.BASE_URL}
      >{$i18n.messages.boliviaFire}</a
    ><a href="#about">{$i18n.messages.aboutLimitations}</a>
    <div
      class="language-control"
      role="group"
      aria-label={$i18n.messages.language}
    >
      <button
        lang="en"
        aria-label={$i18n.messages.english}
        aria-pressed={$i18n.language === "en"}
        onclick={() => void chooseLanguage("en")}
        >{$i18n.messages.enCode}</button
      >
      <button
        lang="es"
        aria-label={$i18n.messages.spanish}
        aria-pressed={$i18n.language === "es"}
        onclick={() => void chooseLanguage("es")}
        >{$i18n.messages.esCode}</button
      >
    </div>
  </div>
</header>
<main>
  {#if languageError}<p role="alert">
      {$i18n.messages.languageLoadError}
    </p>{/if}
  <div class="page-intro">
    <p class="eyebrow">{$i18n.messages.eyebrow}</p>
    <h1>{$i18n.messages.weeklyFireDetections}</h1>
    <p class="intro-copy">
      {staticMode ? $i18n.messages.liveIntro : $i18n.messages.apiIntro}
    </p>
  </div>

  {#if bootLoading}
    <div class="state-panel" role="status">
      <h2>{$i18n.messages.loadingTheDataExplorer}</h2>
      <p>{$i18n.messages.bootHint}</p>
    </div>
  {:else if bootError}
    <div class="state-panel error" role="alert">
      <h2>
        {bootError instanceof ApiError && bootError.kind === "offline"
          ? staticMode
            ? $i18n.messages.dataFilesOffline
            : $i18n.messages.apiOffline
          : $i18n.messages.dataServiceUnavailable}
      </h2>
      <p>{errorText($i18n, bootError)}</p>
      <button class="primary" onclick={() => void boot()}
        >{$i18n.messages.retryConnection}</button
      >
    </div>
  {:else if data}
    <section id="explorer" aria-label={$i18n.messages.dataExplorer}>
      <div class="filter-bar">
        <div class="week-control">
          <label for="issue-week"
            >{$i18n.messages.issueWeek}
            <span class="muted small">{$i18n.messages.mondayUtc}</span></label
          >
          <div class="week-input">
            <button
              aria-label={$i18n.messages.previousWeek}
              disabled={weekIndex <= 0}
              onclick={() => changeWeek(-1)}>←</button
            >{#if staticMode}<select
                id="issue-week"
                bind:value={date}
                onchange={() => {
                  kind = "estimate";
                }}
                aria-describedby="week-hint"
                disabled={!data.weeks.length}
              >
                {#each data.weeks as week}<option value={week.date}
                    >{formatDate(week.date, $i18n.language)}</option
                  >{/each}
              </select>{:else}<input
                id="issue-week"
                type="date"
                bind:value={date}
                min={data.weeks[0]?.date}
                max={data.weeks.at(-1)?.date}
                aria-describedby="week-hint"
                disabled={!data.weeks.length}
              />{/if}<button
              aria-label={$i18n.messages.nextWeek}
              disabled={weekIndex < 0 || weekIndex >= data.weeks.length - 1}
              onclick={() => changeWeek(1)}>→</button
            >
          </div>
        </div>
        <fieldset class="layer-control">
          <legend>{$i18n.messages.layer}</legend>
          <div class="segmented">
            <button
              aria-pressed={kind === "observed"}
              disabled={staticMode && observation?.complete !== true}
              onclick={() => {
                kind = "observed";
              }}>{$i18n.messages.observedDetections}</button
            ><button
              aria-pressed={kind === "estimate"}
              disabled={!staticMode && !estimatesAvailable}
              aria-describedby={!staticMode && !estimatesAvailable
                ? "missing-predictions"
                : undefined}
              onclick={() => {
                kind = "estimate";
              }}
              >{staticMode
                ? $i18n.messages.weeklyEstimate
                : $i18n.messages.retrospectiveEstimate}</button
            >
          </div>
        </fieldset>
        {#if !staticMode}<label for="model" class="model-control"
            >{$i18n.messages.model}<select
              id="model"
              bind:value={model}
              disabled={!availableModels.length}
              ><option value="" disabled>{$i18n.messages.noneAvailable}</option
              >{#each availableModels as option}<option value={option}
                  >{option.replaceAll("_", " ")}</option
                >{/each}</select
            ></label
          >
          <label for="variant" class="variant-control"
            >{$i18n.messages.variant}<select
              id="variant"
              bind:value={variant}
              disabled={!data.meta.variants.length}
              ><option value="" disabled>{$i18n.messages.noneAvailable}</option
              >{#each data.meta.variants as option}<option value={option}
                  >{localizeText($i18n, option.replaceAll("_", " "))}</option
                >{/each}</select
            ></label
          >
        {/if}
      </div>
      <div class="filter-notes">
        <p id="week-hint" class="muted small">
          {$i18n.messages.weekHint}
        </p>
        {#if !staticMode && !estimatesAvailable}<p
            id="missing-predictions"
            class="muted small"
          >
            {$i18n.messages.noEstimatesHint}
          </p>{/if}
      </div>
      {#if !staticMode && kind === "estimate"}<p class="notice">
          {$i18n.messages.retrospectiveNotice}
        </p>{/if}
      {#if staticMode && layer?.forecast}
        <div
          class="forecast-summary"
          aria-label={$i18n.messages.forecastMetadata}
        >
          <p class="forecast-timing">
            {layer.forecast.prospective
              ? $i18n.messages.prospectiveEstimate
              : $i18n.messages.backfillEstimate}
          </p>
          <p>
            {$i18n.messages.issue}
            <strong
              >{formatDate(layer.forecast.issue_date, $i18n.language)}
              {$i18n.messages.text0000Utc}</strong
            >
            {$i18n.messages.target}
            <strong
              >{formatWindow(
                layer.forecast.target_start,
                layer.forecast.target_end,
                $i18n.language,
              )}</strong
            >
          </p>
          <p class="small muted">
            {$i18n.messages.published}
            {formatTime(layer.forecast.generated_utc, $i18n.language)} · {formatUnit(
              layer.forecast.publication_lag_hours,
              "h",
              $i18n.language,
            )}
            {$i18n.messages.hAfterIssueModel}
            {@render forecastModel(layer.forecast)}
          </p>
          <p class="small muted">
            {$i18n.messages.inputWindow}
            {formatDate(layer.forecast.input_window.first_day, $i18n.language)}
            {$i18n.messages.through}
            {formatDate(layer.forecast.input_window.last_day, $i18n.language)}
            {$i18n.messages.text2359UtcBeforeTheIssue}
          </p>
          {#if layer.forecast.input_window.unobserved_days.length}<details
              class="input-history small muted"
              data-testid="input-history"
            >
              <summary
                >{countMessage(
                  $i18n,
                  "inputDaysInThe365Day",
                  layer.forecast.input_window.unobserved_days.length,
                )}</summary
              >
              <ul
                class="outage-dates"
                aria-label={$i18n.messages.unobservedInputDays}
              >
                {#each layer.forecast.input_window.unobserved_days as day}<li>
                    {formatDate(day, $i18n.language)}
                  </li>{/each}
              </ul>
            </details>{/if}
          <details
            class="small muted forecast-provenance"
            data-testid="forecast-provenance"
          >
            <summary>{$i18n.messages.forecastProvenance}</summary>
            <dl>
              <div>
                <dt>{$i18n.messages.exactTargetInterval}</dt>
                <dd>
                  [{layer.forecast.target_start}, {layer.forecast
                    .target_end}{$i18n.messages.utc}
                </dd>
              </div>
              <div>
                <dt>{$i18n.messages.publicationTimestamp}</dt>
                <dd>{layer.forecast.generated_utc}</dd>
              </div>
              <div>
                <dt>{$i18n.messages.modelSha256Identifier}</dt>
                <dd class="hash">{layer.forecast.model_id}</dd>
              </div>
            </dl>
          </details>
        </div>
      {/if}
      {#if staticMode && observationError}<p class="notice">
          {$i18n.messages.observationFileUnavailable}
          {errorText($i18n, observationError)}
        </p>{/if}
      {#if staticMode && currentWeek && !currentWeek.observation}<p
          class="notice"
        >
          {$i18n.messages.observationsPending}
        </p>{/if}
      {#if staticMode && observation?.coverage?.pendingDays.length}<p
          class="target-progress small muted"
          data-testid="target-progress"
        >
          {#if observation.coverage.lastObservedDay}{$i18n.messages
              .targetProgress}
            {formatDate(
              observation.coverage.lastObservedDay,
              $i18n.language,
            )}{:else}{$i18n.messages.targetPending}{/if}
        </p>{/if}
      {#if currentWeek?.targetComplete === false || (staticMode && observation?.coverage?.unobservedDays.length)}<p
          class="notice"
          data-testid="observation-notice"
        >
          {$i18n.messages.outageNotice}
          {#if staticMode}{$i18n.messages.targetOutages}
            {observation?.coverage?.unobservedDays
              .map((day) => formatDate(day, $i18n.language))
              .join(", ")}.{/if}
        </p>{/if}
      {#if currentWeek?.lowCoverage}<p
          class="notice"
          data-testid="low-coverage-notice"
        >
          {$i18n.messages.lowCoverageNotice}
        </p>{/if}
      <div class="workspace">
        <section class="map-panel" aria-label={$i18n.messages.weeklyGrid}>
          <div class="panel-toolbar">
            <div>
              <h2>
                {kind === "observed"
                  ? $i18n.messages.observedDetections
                  : staticMode
                    ? $i18n.messages.weeklyEstimate
                    : $i18n.messages.retrospectiveEstimate}
              </h2>
              <p class="muted small">
                {date
                  ? formatDate(date, $i18n.language)
                  : $i18n.messages
                      .noAvailableIssueWeeks}{#if currentWeek?.completeness != null}
                  {" · "}{formatPercent(
                    currentWeek.completeness,
                    $i18n.language,
                  )}{$i18n.messages.labelCompleteness}{/if}
              </p>
              {#if kind === "estimate" && layer?.model}
                <p class="muted small">
                  {$i18n.messages.model2}
                  {#if staticMode && layer.forecast}{@render forecastModel(
                      layer.forecast,
                    )}{:else}{layer.model}
                    {$i18n.messages.variant2}
                    {layer.variant}{/if}
                </p>
              {/if}
            </div>
            <div class="segmented view-toggle">
              <button
                aria-label={$i18n.messages.mapView}
                aria-pressed={view === "map"}
                onclick={() => {
                  view = "map";
                }}>{$i18n.messages.map}</button
              ><button
                aria-label={$i18n.messages.tableView}
                aria-pressed={view === "table"}
                onclick={() => {
                  view = "table";
                }}>{$i18n.messages.table}</button
              >
            </div>
          </div>
          <p class="data-status" data-testid="layer-status" role="status">
            {layerLoading
              ? $i18n.messages.loadingWeeklyValues
              : layerError
                ? layerError instanceof ApiError &&
                  layerError.kind === "missing" &&
                  kind === "estimate"
                  ? $i18n.messages.missingSelection
                  : errorText($i18n, layerError)
                : summary || $i18n.messages.emptySnapshot}
          </p>
          {#if layerLoading}<div class="map-state" aria-busy="true">
              <p>{$i18n.messages.loadingWeeklyValues}</p>
            </div>
          {:else if layerError}<div class="map-state error" role="alert">
              <h3>
                {layerError instanceof ApiError &&
                layerError.kind === "missing" &&
                kind === "estimate"
                  ? $i18n.messages.missingPredictions
                  : layerError instanceof ApiError &&
                      layerError.kind === "offline"
                    ? staticMode
                      ? $i18n.messages.dataFilesOffline
                      : $i18n.messages.apiOffline
                    : $i18n.messages.selectionUnavailable}
              </h3>
              <p>{errorText($i18n, layerError)}</p>
              <button
                onclick={() => {
                  retry++;
                }}>{$i18n.messages.retryWeeklyData}</button
              >
            </div>
          {:else if !data.grid.cells.length || !layer?.cell_index.length}<div
              class="map-state"
            >
              <h3>{$i18n.messages.noWeeklyData}</h3>
              <p>
                {$i18n.messages.emptySnapshot}
              </p>
            </div>
          {:else if view === "map"}{#await import("./components/CellMap.svelte") then module}<module.default
                grid={data.grid}
                boundaries={data.boundaries}
                {values}
                {kind}
                {selected}
                {showHoldout}
                onselect={(cell) => {
                  selected = cell;
                }}
              />{/await}
          {:else}{#await import("./components/CellTable.svelte") then module}<module.default
                grid={data.grid}
                {values}
                {counts}
                {kind}
                {selected}
                onselect={(cell) => {
                  selected = cell;
                }}
              />{/await}{/if}
          <div class="legend" aria-label={$i18n.messages.mapLegend}>
            {#if holdoutAvailable}<span
                ><i class="swatch holdout"></i>{$i18n.messages
                  .geographicHoldout}</span
              >{/if}
            {#if holdoutUnavailable}<span
                ><i class="swatch holdout unavailable-holdout"></i>{$i18n
                  .messages.geographicUnavailable}</span
              >{/if}
            {#if kind === "estimate"}<div class="probability-legend">
                <span>{$i18n.messages.probability}</span>
                <div class="color-scale"></div>
                <div class="scale-labels">
                  <span>0</span><span
                    >{formatNumber(0.5, $i18n.language, 1)}</span
                  ><span>1</span>
                </div>
              </div>
            {:else}<span
                ><i class="swatch detected"></i>{$i18n.messages.detected}</span
              ><span
                ><i class="swatch negative"></i>{$i18n.messages
                  .notDetected}</span
              >{/if}
            <span
              ><i class="swatch missing"></i>{$i18n.messages
                .missingObservation}{kind === "estimate"
                ? ` ${$i18n.messages.estimate}`
                : ""}</span
            >
          </div>
          <div class="panel-footer">
            <p class="small muted">
              {$i18n.messages.missingHint}
            </p>
            {#if csvUrl && layer && !layerLoading && !layerError}<a
                class="download-link"
                href={csvUrl}
                download={`bolivia-fire_${date}_${kind}.csv`}
                >{$i18n.messages.downloadWeekCsv}</a
              >{/if}
          </div>
        </section>
        <aside class="detail-panel" aria-label={$i18n.messages.cellDetail}>
          <div class="detail-heading">
            <h2>{$i18n.messages.cellDetail}</h2>
            <span class="muted small"
              >{$i18n.messages.nationalIntersection}</span
            >
          </div>
          {#if selected}
            <h3 class="cell-id" data-testid="selected-cell">{selected.id}</h3>
            <CellPlaces cell={selected} />
            <dl class="cell-facts">
              <div>
                <dt>{$i18n.messages.nationalArea}</dt>
                <dd>
                  {formatUnit(
                    selected.area,
                    $i18n.messages.km,
                    $i18n.language,
                    2,
                  )}
                </dd>
              </div>
              <div>
                <dt>
                  {kind === "estimate"
                    ? $i18n.messages.currentProbability
                    : $i18n.messages.currentObservation}
                </dt>
                <dd>
                  {layerLoading
                    ? $i18n.messages.loading
                    : layerError
                      ? $i18n.messages.unavailable
                      : formatValue(values.get(selected.index), kind, $i18n)}
                </dd>
              </div>
            </dl>
            {#if kind === "estimate" && !staticMode}
              <p class="small" data-testid="value-model">
                {$i18n.messages.valueModel}: {selectedValueModel ??
                  $i18n.messages.unavailable}
              </p>
              {#if showHoldout && selected.geoHoldout}<p
                  class="small muted"
                  data-testid="holdout-detail"
                >
                  {values.get(selected.index) == null
                    ? $i18n.messages.geographicUnavailable
                    : $i18n.messages.geographicHoldout}
                </p>{/if}
            {/if}
            <div class="series-heading">
              <h3>{$i18n.messages.weeklyHistory}</h3>
              <p class="small muted">
                {$i18n.messages.seriesKey}
              </p>
            </div>
            <div class="range-controls">
              <label for="range-start"
                >{$i18n.messages.from}<input
                  id="range-start"
                  type="date"
                  bind:value={rangeStart}
                  min={data.meta.start || undefined}
                  max={rangeEnd || undefined}
                /></label
              ><label for="range-end"
                >{$i18n.messages.to}<input
                  id="range-end"
                  type="date"
                  bind:value={rangeEnd}
                  min={rangeStart || undefined}
                  max={data.meta.end || undefined}
                /></label
              >
            </div>
            {#if seriesLoading}<p class="muted" role="status">
                {$i18n.messages.loadingCellHistory}
              </p>{:else if seriesError}<p class="error-text" role="alert">
                {errorText($i18n, seriesError)}
              </p>{:else}{#await import("./components/SeriesChart.svelte") then module}<module.default
                  points={series}
                />{/await}{/if}
            <p class="small muted series-note">
              {$i18n.messages.observedYIs1WhenAt}
            </p>
          {:else}<div class="selection-empty">
              <h3>{$i18n.messages.selectAGridCell}</h3>
              <p>
                {$i18n.messages.clickTheMapOrChooseA}
              </p>
              <p class="small muted">
                {$i18n.messages.keyboardHint}
              </p>
            </div>{/if}
        </aside>
      </div>
    </section>
    {#if staticMode}{#await import("./components/TrackRecord.svelte") then module}<module.default
        />{/await}{/if}
    {#await import("./components/About.svelte") then module}<module.default
        meta={data.meta}
      />{/await}
  {/if}
  <footer class="page-footer">
    {$i18n.messages.boliviaWeeklyDetectionBenchmarkResearchData}
  </footer>
</main>
