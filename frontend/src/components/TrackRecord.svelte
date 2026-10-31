<script lang="ts">
  import {
    i18n,
    formatNumber,
    formatDate,
    formatTime,
    localizeText,
    errorText,
  } from "../lib/i18n";
  import { en } from "../lib/messages/en";
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
      title: $i18n.messages.prospectiveWeeks,
      label: $i18n.messages.weeklyVerificationScores,
      rows: prospective,
    },
    {
      title: $i18n.messages.backfillWeeksGeneratedAfterIssueTime,
      label: $i18n.messages.backfillVerificationScores,
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
          cause instanceof Error ? cause : new Error(en.trackRecordUnavailable);
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
    value === null
      ? $i18n.messages.undefined
      : formatNumber(value, $i18n.language, 3);
</script>

<section class="track-panel" aria-label={$i18n.messages.trackRecord}>
  <h2>{$i18n.messages.trackRecord}</h2>
  <p class="muted small">
    {$i18n.messages.verificationHint}
  </p>
  {#if loading}<p role="status">{$i18n.messages.loadingVerificationScores}</p>
  {:else if error}<div role="alert" class="error-text">
      <p>{errorText($i18n, error)}</p>
      <button onclick={() => void load()}
        >{$i18n.messages.retryTrackRecord}</button
      >
    </div>
  {:else if !records.length}<p class="empty-state">
      {$i18n.messages.noVerifiedWeeksYet}
    </p>
  {:else}
    {#if prospective.length}
      <p class="small muted">
        {$i18n.messages.prospectiveHint}
      </p>
      <svg
        class="track-chart"
        viewBox="0 0 560 185"
        role="img"
        aria-label={$i18n.messages.verificationChartLabel}
      >
        {#each [0, 0.5, 1] as tick}
          <line x1="42" x2="512" y1={y(tick)} y2={y(tick)} stroke="#d1dada" />
          <text x="30" y={y(tick) + 4} text-anchor="end"
            >{formatNumber(tick, $i18n.language, tick === 0.5 ? 1 : 0)}</text
          >
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
              ><title
                >{formatDate(record.issue_date, $i18n.language)}{$i18n.messages
                  .ap}
                {metric(record.ap)}</title
              ></circle
            >{/if}
          {#if record.climatology_ap !== null}<circle
              cx={x(i)}
              cy={y(record.climatology_ap)}
              r="2"
              fill="#8b4a18"
              ><title
                >{formatDate(record.issue_date, $i18n.language)}{$i18n.messages
                  .climatologyAp}
                {metric(record.climatology_ap)}</title
              ></circle
            >{/if}
        {/each}
        <text x="42" y="172"
          >{formatDate(prospective[0].issue_date, $i18n.language)}</text
        >
        {#if prospective.length > 1}<text x="512" y="172" text-anchor="end"
            >{formatDate(prospective.at(-1)!.issue_date, $i18n.language)}</text
          >{/if}
      </svg>
      <p class="chart-key">
        <span><i class="line-key"></i>{$i18n.messages.estimateAp}</span><span
          ><i class="baseline-key"></i>{$i18n.messages.climatologyAp2}</span
        >
      </p>
    {:else}<p class="empty-state">
        {$i18n.messages.noProspectiveVerifiedWeeksYet}
      </p>{/if}
    {#each groups as group}{#if group.rows.length}
        <h3>{group.title}</h3>
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (The scrolling table needs keyboard access.) -->
        <div
          class="table-scroll"
          tabindex="0"
          role="region"
          aria-label={$i18n.messages.verificationScroll}
        >
          <table aria-label={group.label}>
            <thead
              ><tr
                ><th scope="col">{$i18n.messages.issueWeek}</th><th scope="col"
                  >{$i18n.messages.publishedUtc}</th
                ><th scope="col">{$i18n.messages.lagHours}</th><th scope="col"
                  >{$i18n.messages.cells2}</th
                ><th scope="col">{$i18n.messages.positives}</th><th scope="col"
                  >{$i18n.messages.ap2}</th
                ><th scope="col">{$i18n.messages.brier}</th><th scope="col"
                  >{$i18n.messages.recallAt5Area}</th
                ><th scope="col">{$i18n.messages.recallAt10Area}</th><th
                  scope="col">{$i18n.messages.climatologyAp2}</th
                ><th scope="col">{$i18n.messages.rocAuc}</th><th scope="col"
                  >{$i18n.messages.ece}</th
                ><th scope="col">{$i18n.messages.notes}</th></tr
              ></thead
            >
            <tbody
              >{#each group.rows as record}<tr
                  ><th scope="row"
                    >{formatDate(record.issue_date, $i18n.language)}</th
                  ><td>{formatTime(record.generated_utc, $i18n.language)}</td
                  ><td
                    >{formatNumber(
                      record.publication_lag_hours,
                      $i18n.language,
                    )}</td
                  ><td>{formatNumber(record.n_cells, $i18n.language)}</td><td
                    >{formatNumber(record.positives, $i18n.language)}</td
                  ><td>{metric(record.ap)}</td><td>{metric(record.brier)}</td
                  ><td>{metric(record.recall_area_5)}</td><td
                    >{metric(record.recall_area_10)}</td
                  ><td>{metric(record.climatology_ap)}</td><td
                    >{metric(record.roc_auc)}</td
                  ><td>{metric(record.ece)}</td><td
                    >{localizeText($i18n, record.notes)}</td
                  ></tr
                >{/each}</tbody
            >
          </table>
        </div>
      {/if}{/each}
    <p class="muted small">
      {$i18n.messages.metricHint}
    </p>
  {/if}
</section>
