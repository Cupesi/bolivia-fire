<script lang="ts">
  import { formatValue } from "../lib/color";
  import type { Cell, Grid, LayerKind, NullableNumber } from "../lib/types";
  let {
    grid,
    values,
    kind,
    onselect,
  }: {
    grid: Grid;
    values: Map<number, NullableNumber>;
    kind: LayerKind;
    onselect: (cell: Cell) => void;
  } = $props();
  let sort = $state<"id" | "department" | "area" | "value">("value");
  let descending = $state(true);
  let top = $state("100");
  let page = $state(0);
  const limit = 25;
  const ranked = $derived.by(() =>
    [...grid.cells]
      .sort(
        (a, b) =>
          (values.get(b.index) ?? -Infinity) -
            (values.get(a.index) ?? -Infinity) || a.id.localeCompare(b.id),
      )
      .slice(0, top === "all" ? undefined : Number(top)),
  );
  const sorted = $derived.by(() =>
    [...ranked].sort((a, b) => {
      if (sort === "value") {
        const first = values.get(a.index);
        const second = values.get(b.index);
        if (first == null || second == null)
          return first == null
            ? second == null
              ? a.id.localeCompare(b.id)
              : 1
            : -1;
        return (
          (first - second) * (descending ? -1 : 1) || a.id.localeCompare(b.id)
        );
      }
      const result =
        sort === "area" ? a.area - b.area : a[sort].localeCompare(b[sort]);
      return result * (descending ? -1 : 1) || a.id.localeCompare(b.id);
    }),
  );
  const pageCount = $derived(Math.max(1, Math.ceil(sorted.length / limit)));
  const safePage = $derived(Math.min(page, pageCount - 1));
  const visible = $derived(
    sorted.slice(safePage * limit, (safePage + 1) * limit),
  );
  function changeSort(next: typeof sort) {
    descending =
      sort === next ? !descending : next === "value" || next === "area";
    sort = next;
    page = 0;
  }
  function ariaSort(column: typeof sort): "ascending" | "descending" | "none" {
    return sort === column ? (descending ? "descending" : "ascending") : "none";
  }
</script>

<div class="table-toolbar">
  <label for="top-cells"
    >Top cells<select
      id="top-cells"
      bind:value={top}
      onchange={() => {
        page = 0;
      }}
      ><option value="50">Top 50 by value</option><option value="100"
        >Top 100 by value</option
      ><option value="500">Top 500 by value</option><option value="all"
        >All cells</option
      ></select
    ></label
  >
  <p class="muted small">
    Missing values sort last. Each page contains up to {limit} cells.
  </p>
</div>
<div class="table-scroll">
  <table aria-label="Current week cells">
    <caption class="sr-only"
      >Current week grid values, ranked by value before sorting and pagination.</caption
    ><thead
      ><tr>
        <th aria-sort={ariaSort("id")}
          ><button onclick={() => changeSort("id")} aria-label="Sort by cell id"
            >Cell id</button
          ></th
        >
        <th aria-sort={ariaSort("department")}
          ><button
            onclick={() => changeSort("department")}
            aria-label="Sort by department">Department</button
          ></th
        >
        <th aria-sort={ariaSort("area")}
          ><button
            onclick={() => changeSort("area")}
            aria-label="Sort by national area">Area (km²)</button
          ></th
        >
        <th aria-sort={ariaSort("value")}
          ><button
            onclick={() => changeSort("value")}
            aria-label="Sort by value"
            >{kind === "estimate" ? "Probability" : "Observation"}</button
          ></th
        >
      </tr></thead
    ><tbody
      >{#each visible as cell}<tr
          ><td
            ><button
              class="text-button"
              aria-label={`Select cell ${cell.id}`}
              onclick={() => onselect(cell)}>{cell.id}</button
            ></td
          ><td>{cell.department || "Unassigned"}</td><td class="numeric"
            >{cell.area.toFixed(2)}</td
          ><td>{formatValue(values.get(cell.index), kind)}</td></tr
        >{/each}</tbody
    >
  </table>
</div>
{#if !visible.length}<p class="empty-state">
    No cells are available for this week.
  </p>{/if}
<div class="pagination">
  <button
    aria-label="Previous table page"
    disabled={safePage === 0}
    onclick={() => {
      page = safePage - 1;
    }}>Previous</button
  ><span data-testid="table-page"
    >Page {safePage + 1} of {pageCount} · {ranked.length.toLocaleString("en")} cells</span
  ><button
    aria-label="Next table page"
    disabled={safePage >= pageCount - 1}
    onclick={() => {
      page = safePage + 1;
    }}>Next</button
  >
</div>
