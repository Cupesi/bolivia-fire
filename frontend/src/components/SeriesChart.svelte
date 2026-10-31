<script lang="ts">
  import { i18n, message, formatNumber, formatDate } from "../lib/i18n";
  import type { SeriesPoint } from "../lib/types";
  let { points }: { points: SeriesPoint[] } = $props();
  const x = (index: number) =>
    38 + (index / Math.max(1, points.length - 1)) * 294;
  const y = (value: number) => 112 - value * 88;
  const path = $derived.by(() => {
    let drawing = false;
    return points
      .map((point, index) => {
        if (point.p === null) {
          drawing = false;
          return "";
        }
        const segment = `${drawing ? "L" : "M"}${x(index)},${y(point.p)}`;
        drawing = true;
        return segment;
      })
      .join(" ");
  });
</script>

{#if points.length}
  <svg
    class="series-chart"
    viewBox="0 0 356 155"
    role="img"
    aria-label={$i18n.messages.seriesChartLabel}
  >
    <line
      x1="38"
      x2="332"
      y1="24"
      y2="24"
      stroke="#d1dada"
      stroke-dasharray="3 3"
    />
    <line x1="38" x2="332" y1="68" y2="68" stroke="#e0e6e5" />
    <line x1="38" x2="332" y1="112" y2="112" stroke="#b4c4c0" />
    <text x="22" y="28" text-anchor="end">1</text><text
      x="22"
      y="72"
      text-anchor="end">{formatNumber(0.5, $i18n.language, 1)}</text
    ><text x="22" y="116" text-anchor="end">0</text>
    {#each points as point, index}
      {#if point.y !== null}<circle
          cx={x(index)}
          cy={y(point.y)}
          r="2.6"
          fill="#8b4a18"
          ><title
            >{message($i18n, "observedPoint", {
              date: formatDate(point.date, $i18n.language),
              y: point.y,
              n:
                point.n == null
                  ? $i18n.messages.missing
                  : formatNumber(point.n, $i18n.language),
            })}</title
          ></circle
        >
      {:else}<line
          x1={x(index)}
          x2={x(index)}
          y1="122"
          y2="128"
          stroke="#727d80"
          ><title
            >{message($i18n, "missingPoint", {
              date: formatDate(point.date, $i18n.language),
            })}</title
          ></line
        >{/if}
    {/each}
    <path d={path} fill="none" stroke="#176577" stroke-width="2" />
    {#each points as point, index}{#if point.p !== null}<circle
          cx={x(index)}
          cy={y(point.p)}
          r="1.4"
          fill="#176577"
          ><title
            >{message($i18n, "probabilityPoint", {
              date: formatDate(point.date, $i18n.language),
              p: formatNumber(point.p, $i18n.language, 3),
            })}</title
          ></circle
        >{/if}{/each}
    <text x="38" y="147">{formatDate(points[0].date, $i18n.language)}</text
    ><text x="332" y="147" text-anchor="end"
      >{formatDate(points.at(-1)!.date, $i18n.language)}</text
    >
  </svg>
  <div class="chart-key">
    <span><i class="line-key"></i>{$i18n.messages.estimateP}</span><span
      ><i class="dot-key"></i>{$i18n.messages.observedY2}</span
    ><span><i class="missing-key"></i>{$i18n.messages.missingY}</span>
  </div>
  {#if points.every((point) => point.p === null)}<p class="muted small">
      {$i18n.messages.noPredictionsInThisRangeObservations}
    </p>{/if}
  <details class="series-data">
    <summary>{$i18n.messages.readWeeklyValues}</summary>
    <div class="table-scroll">
      <table aria-label={$i18n.messages.selectedCellWeeklySeries}>
        <thead
          ><tr
            ><th>{$i18n.messages.issueWeek}</th><th
              >{$i18n.messages.observedY2}</th
            ><th>{$i18n.messages.detections2}</th><th
              >{$i18n.messages.estimateP}</th
            ></tr
          ></thead
        ><tbody
          >{#each points as point}<tr
              ><td>{formatDate(point.date, $i18n.language)}</td><td
                >{point.y ?? $i18n.messages.missing}</td
              ><td
                >{point.n == null
                  ? $i18n.messages.missing
                  : formatNumber(point.n, $i18n.language)}</td
              ><td
                >{point.p == null
                  ? $i18n.messages.missing
                  : formatNumber(point.p, $i18n.language, 3)}</td
              ></tr
            >{/each}</tbody
        >
      </table>
    </div>
  </details>
{:else}<p class="muted">{$i18n.messages.noWeeklyRecordsInThisRange}</p>{/if}
