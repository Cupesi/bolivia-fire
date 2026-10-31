<script lang="ts">
  import { onMount } from "svelte";
  import { liveClient } from "../lib/data-client";
  import type { TrackRecord } from "../lib/types";
  let records = $state<TrackRecord[]>([]);
  let loading = $state(true);
  let error = $state<Error | null>(null);
  let controller: AbortController | null = null;
  const prospective = $derived(records.filter((record) => record.prospective));
  const groups = $derived([
    {
      title: "Prospective weeks",
      label: "Weekly verification scores",
      rows: prospective,
    },
    {
      title: "Backfill weeks · generated after issue time",
      label: "Backfill verification scores",
      rows: records.filter((record) => !record.prospective),
    },
  ]);
  async function load() {
    controller?.abort();
    const request = new AbortController();
    controller = request;
    loading = true;
    error = null;
    try {
      const result = await liveClient.loadTrackRecord(request.signal);
      if (!request.signal.aborted) records = result;
    } catch (cause) {
      if (!request.signal.aborted)
        error =
          cause instanceof Error
            ? cause
            : new Error("Track record unavailable.");
    } finally {
      if (!request.signal.aborted) loading = false;
    }
  }
  onMount(() => {
    void load();
    return () => controller?.abort();
  });
  const x = (i: number) => 42 + (i / Math.max(1, prospective.length - 1)) * 470;
  const y = (value: number) => 142 - value * 110;
  function path(key: "ap" | "climatology_ap") {
    let drawing = false;
    return prospective
      .map((record, i) => {
        const value = record[key];
        if (value === null) {
          drawing = false;
          return "";
        }
        const point = `${drawing ? "L" : "M"}${x(i)},${y(value)}`;
        drawing = true;
        return point;
      })
      .join(" ");
  }
  const metric = (value: number | null) =>
    value === null ? "Undefined" : value.toFixed(3);
</script>

<section class="track-panel" aria-label="Track record">
  <h2>Track record</h2>
  <p class="muted small">
    Completed target weeks only. Climatology uses the same week and cells. A few
    weeks give limited evidence about performance.
  </p>
  {#if loading}<p role="status">Loading verification scores…</p>
  {:else if error}<div role="alert" class="error-text">
      <p>{error.message}</p>
      <button onclick={() => void load()}>Retry track record</button>
    </div>
  {:else if !records.length}<p class="empty-state">No verified weeks yet.</p>
  {:else}
    {#if prospective.length}
      <p class="small muted">
        The chart shows prospective weeks only. Backfills do not measure
        prospective performance.
      </p>
      <svg
        class="track-chart"
        viewBox="0 0 560 185"
        role="img"
        aria-label="Weekly average precision compared with training climatology for prospective weeks only, from zero to one"
      >
        {#each [0, 0.5, 1] as tick}
          <line x1="42" x2="512" y1={y(tick)} y2={y(tick)} stroke="#d1dada" />
          <text x="30" y={y(tick) + 4} text-anchor="end">{tick}</text>
        {/each}
        <path
          d={path("climatology_ap")}
          fill="none"
          stroke="#8b4a18"
          stroke-width="2"
          stroke-dasharray="5 4"
        />
        <path d={path("ap")} fill="none" stroke="#176577" stroke-width="2" />
        {#each prospective as record, i}
          {#if record.ap !== null}<circle
              cx={x(i)}
              cy={y(record.ap)}
              r="3"
              fill="#176577"
              ><title>{record.issue_date}: AP {metric(record.ap)}</title
              ></circle
            >{/if}
          {#if record.climatology_ap !== null}<circle
              cx={x(i)}
              cy={y(record.climatology_ap)}
              r="2"
              fill="#8b4a18"
              ><title
                >{record.issue_date}: climatology AP {metric(
                  record.climatology_ap,
                )}</title
              ></circle
            >{/if}
        {/each}
        <text x="42" y="172">{prospective[0].issue_date}</text>
        {#if prospective.length > 1}<text x="512" y="172" text-anchor="end"
            >{prospective.at(-1)!.issue_date}</text
          >{/if}
      </svg>
      <p class="chart-key">
        <span><i class="line-key"></i>Estimate AP</span><span
          ><i class="baseline-key"></i>Climatology AP</span
        >
      </p>
    {:else}<p class="empty-state">No prospective verified weeks yet.</p>{/if}
    {#each groups as group}{#if group.rows.length}
        <h3>{group.title}</h3>
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (The scrolling table needs keyboard access.) -->
        <div
          class="table-scroll"
          tabindex="0"
          role="region"
          aria-label="Verification scores, scroll horizontally for all metrics"
        >
          <table aria-label={group.label}>
            <thead
              ><tr
                ><th scope="col">Issue week</th><th scope="col"
                  >Published UTC</th
                ><th scope="col">Lag (hours)</th><th scope="col">Cells</th><th
                  scope="col">Positives</th
                ><th scope="col">AP</th><th scope="col">Brier</th><th
                  scope="col">Recall at 5% area</th
                ><th scope="col">Recall at 10% area</th><th scope="col"
                  >Climatology AP</th
                ><th scope="col">ROC AUC</th><th scope="col">ECE</th><th
                  scope="col">Notes</th
                ></tr
              ></thead
            >
            <tbody
              >{#each group.rows as record}<tr
                  ><th scope="row">{record.issue_date}</th><td
                    >{record.generated_utc}</td
                  ><td>{record.publication_lag_hours.toFixed(1)}</td><td
                    >{record.n_cells.toLocaleString("en")}</td
                  ><td>{record.positives.toLocaleString("en")}</td><td
                    >{metric(record.ap)}</td
                  ><td>{metric(record.brier)}</td><td
                    >{metric(record.recall_area_5)}</td
                  ><td>{metric(record.recall_area_10)}</td><td
                    >{metric(record.climatology_ap)}</td
                  ><td>{metric(record.roc_auc)}</td><td>{metric(record.ece)}</td
                  ><td>{record.notes}</td></tr
                >{/each}</tbody
            >
          </table>
        </div>
      {/if}{/each}
    <p class="muted small">
      AP is average precision; higher is better. Lower Brier and expected
      calibration error (ECE) are better. Undefined scores are missing, never
      zero.
    </p>
  {/if}
</section>
