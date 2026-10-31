<script lang="ts">
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
    aria-label="Weekly observed detection indicator y and estimated probability p, from zero to one"
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
      text-anchor="end">0.5</text
    ><text x="22" y="116" text-anchor="end">0</text>
    {#each points as point, index}
      {#if point.y !== null}<circle
          cx={x(index)}
          cy={y(point.y)}
          r="2.6"
          fill="#8b4a18"
          ><title
            >{point.date}: observed y = {point.y}; detections = {point.n ??
              "missing"}</title
          ></circle
        >
      {:else}<line
          x1={x(index)}
          x2={x(index)}
          y1="122"
          y2="128"
          stroke="#727d80"
          ><title>{point.date}: missing observation</title></line
        >{/if}
    {/each}
    <path d={path} fill="none" stroke="#176577" stroke-width="2" />
    {#each points as point, index}{#if point.p !== null}<circle
          cx={x(index)}
          cy={y(point.p)}
          r="1.4"
          fill="#176577"
          ><title>{point.date}: p = {point.p.toFixed(3)}</title></circle
        >{/if}{/each}
    <text x="38" y="147">{points[0].date}</text><text
      x="332"
      y="147"
      text-anchor="end">{points.at(-1)!.date}</text
    >
  </svg>
  <div class="chart-key">
    <span><i class="line-key"></i>Estimate p</span><span
      ><i class="dot-key"></i>Observed y</span
    ><span><i class="missing-key"></i>Missing y</span>
  </div>
  {#if points.every((point) => point.p === null)}<p class="muted small">
      No predictions in this range. Observations remain available.
    </p>{/if}
  <details class="series-data">
    <summary>Read weekly values</summary>
    <div class="table-scroll">
      <table aria-label="Selected cell weekly series">
        <thead
          ><tr
            ><th>Issue week</th><th>Observed y</th><th>Detections</th><th
              >Estimate p</th
            ></tr
          ></thead
        ><tbody
          >{#each points as point}<tr
              ><td>{point.date}</td><td>{point.y ?? "Missing"}</td><td
                >{point.n ?? "Missing"}</td
              ><td>{point.p?.toFixed(3) ?? "Missing"}</td></tr
            >{/each}</tbody
        >
      </table>
    </div>
  </details>
{:else}<p class="muted">No weekly records in this range.</p>{/if}
