<script lang="ts">
  import {
    nearestPlaceLabel,
    tableCells,
    type CellSort,
  } from "../lib/cell-table";
  import type { Cell, Grid, LayerKind, NullableNumber } from "../lib/types";
  let {
    grid,
    values,
    counts,
    kind,
    selected,
    onselect,
  }: {
    grid: Grid;
    values: Map<number, NullableNumber>;
    counts: Map<number, NullableNumber>;
    kind: LayerKind;
    selected: Cell | null;
    onselect: (cell: Cell) => void;
  } = $props();
  let sort = $state<CellSort>("value");
  let descending = $state(true);
  let query = $state("");
  let top = $state("100");
  let page = $state(0);
  $effect(() => {
    if (kind === "estimate" && sort === "count") {
      sort = "value";
      descending = true;
      page = 0;
    }
  });
  const limit = 25;
  const columns = $derived([
    { key: "id", label: "Cell", aria: "cell id" },
    { key: "municipality", label: "Municipality", aria: "municipality" },
    { key: "province", label: "Province", aria: "province" },
    { key: "department", label: "Department", aria: "department" },
    { key: "nearestPlace", label: "Nearest place", aria: "nearest place" },
    { key: "area", label: "Area (km²)", aria: "national area" },
    {
      key: "value",
      label: kind === "estimate" ? "Probability" : "Detected",
      aria: "value",
    },
    ...(kind === "observed"
      ? [{ key: "count", label: "Count", aria: "count" }]
      : []),
  ] as { key: CellSort; label: string; aria: string }[]);
  const sorted = $derived(
    tableCells(grid.cells, values, counts, query, top, sort, descending),
  );
  const pageCount = $derived(Math.max(1, Math.ceil(sorted.length / limit)));
  const safePage = $derived(Math.min(page, pageCount - 1));
  const visible = $derived(
    sorted.slice(safePage * limit, (safePage + 1) * limit),
  );
  function changeSort(next: CellSort) {
    descending =
      sort === next
        ? !descending
        : next === "value" || next === "area" || next === "count";
    sort = next;
    page = 0;
  }
  function rowKeyboard(event: KeyboardEvent, index: number) {
    const offset =
      event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (!offset) return;
    event.preventDefault();
    const row = (event.currentTarget as HTMLElement).closest("tr");
    const next = row?.parentElement?.children[index + offset];
    next?.querySelector("button")?.focus();
  }
</script>

<div class="cell-table" class:observed={kind === "observed"}>
  <div class="table-toolbar">
    <label class="place-filter" for="place-filter"
      >Filter places<input
        id="place-filter"
        type="search"
        bind:value={query}
        placeholder="Municipality, province, department or place"
        oninput={() => {
          page = 0;
        }}
      /></label
    >
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
    <div class="cell-sort">
      <label for="cell-sort"
        >Sort cells by<select
          id="cell-sort"
          value={sort}
          onchange={(event) =>
            changeSort(event.currentTarget.value as CellSort)}
          >{#each columns as column}<option value={column.key}
              >{column.label}</option
            >{/each}</select
        ></label
      ><button
        aria-label="Reverse cell sort"
        onclick={() => {
          descending = !descending;
          page = 0;
        }}>{descending ? "Descending ↓" : "Ascending ↑"}</button
      >
    </div>
    <p class="muted small" id="cell-table-hint">
      Filter before ranking. Missing values sort last. Arrow keys move between
      rows; Enter selects.
    </p>
  </div>
  <table aria-label="Current week cells" aria-describedby="cell-table-hint">
    <caption class="sr-only"
      >Current week grid values, filtered by place, ranked by value before
      sorting and pagination. Detected is 1 for a detection and 0 otherwise.
      Count is the number of detections.</caption
    >
    <thead
      ><tr
        >{#each columns as column}<th
            scope="col"
            data-column={column.key}
            class:numeric={["area", "value", "count"].includes(column.key)}
            aria-sort={sort === column.key
              ? descending
                ? "descending"
                : "ascending"
              : "none"}
            ><button
              onclick={() => changeSort(column.key)}
              aria-label={`Sort by ${column.aria}`}>{column.label}</button
            ></th
          >{/each}</tr
      ></thead
    >
    <tbody
      >{#each visible as cell, index (cell.id)}<tr
          data-cell-id={cell.id}
          class:selected={selected?.id === cell.id}
        >
          <td data-column="id"
            ><button
              class="text-button"
              aria-label={`Select cell ${cell.id}`}
              aria-pressed={selected?.id === cell.id}
              onclick={() => onselect(cell)}
              onkeydown={(event) => rowKeyboard(event, index)}>{cell.id}</button
            ></td
          >
          <td data-column="municipality"
            >{cell.municipality || "Unassigned"}
            <div class="place-secondary">
              <span>{cell.province || "Unassigned"}</span><span
                >{nearestPlaceLabel(cell)}</span
              >
            </div>
            <span class="place-department"
              >{cell.department || "Unassigned"}</span
            ></td
          >
          <td data-column="province">{cell.province || "Unassigned"}</td>
          <td data-column="department">{cell.department || "Unassigned"}</td>
          <td data-column="nearestPlace">{nearestPlaceLabel(cell)}</td>
          <td data-column="area" class="numeric">{cell.area.toFixed(2)}</td>
          <td data-column="value" class="numeric"
            >{values.get(cell.index) == null
              ? "Missing"
              : kind === "estimate"
                ? values.get(cell.index)!.toFixed(3)
                : values.get(cell.index)}</td
          >
          {#if kind === "observed"}<td data-column="count" class="numeric"
              >{counts.get(cell.index) ?? "Missing"}</td
            >{/if}
        </tr>{/each}</tbody
    >
  </table>
  {#if !visible.length}<p class="empty-state" role="status">
      {query.trim()
        ? "No cells match this place filter."
        : "No cells are available for this week."}
    </p>{/if}
  <div class="pagination">
    <button
      aria-label="Previous table page"
      disabled={safePage === 0}
      onclick={() => {
        page = safePage - 1;
      }}>Previous</button
    ><span data-testid="table-page" role="status"
      >Page {safePage + 1} of {pageCount} · {sorted.length.toLocaleString("en")} cells</span
    ><button
      aria-label="Next table page"
      disabled={safePage >= pageCount - 1}
      onclick={() => {
        page = safePage + 1;
      }}>Next</button
    >
  </div>
</div>

<style>
  .cell-table {
    container-type: inline-size;
  }
  .table-toolbar {
    flex-wrap: wrap;
    flex-direction: row;
    align-items: end;
    gap: 12px;
    padding: 12px;
  }
  .place-filter {
    flex: 1 1 220px;
    min-width: 0;
  }
  input {
    width: 100%;
    font-size: 12px;
  }
  .cell-sort {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 8px;
  }
  .table-toolbar p {
    flex-basis: 100%;
    max-width: none;
  }
  table {
    table-layout: fixed;
  }
  th,
  td {
    padding: 10px 8px;
    overflow-wrap: anywhere;
    text-align: left;
  }
  th[data-column="id"] {
    width: 14%;
  }
  th[data-column="municipality"] {
    width: 17%;
  }
  th[data-column="province"] {
    width: 14%;
  }
  th[data-column="department"] {
    width: 13%;
  }
  th[data-column="nearestPlace"] {
    width: 22%;
  }
  th[data-column="area"],
  th[data-column="value"] {
    width: 10%;
  }
  .observed th[data-column="nearestPlace"] {
    width: 17%;
  }
  .observed th[data-column="municipality"] {
    width: 15%;
  }
  .observed th[data-column="count"] {
    width: 7%;
  }
  th button {
    width: 100%;
    text-align: inherit;
  }
  .numeric {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .text-button {
    font-size: 11px;
    text-align: left;
  }
  .text-button::after {
    content: "";
    position: absolute;
    inset: 0;
  }
  tr {
    position: relative;
  }
  tr:hover,
  tr:focus-within,
  tr.selected {
    background: #edf4f1;
  }
  tr:focus-within {
    outline: 3px solid #135b9a;
    outline-offset: -3px;
  }
  .place-secondary,
  .place-department {
    display: none;
    color: #5c7068;
    font-size: 11px;
    line-height: 1.5;
  }
  @container (max-width: 760px) {
    [data-column="province"],
    [data-column="nearestPlace"] {
      display: none;
    }
    .place-secondary,
    .place-secondary span {
      display: block;
    }
    th[data-column="id"] {
      width: 18%;
    }
    th[data-column="municipality"],
    .observed th[data-column="municipality"] {
      width: 32%;
    }
    th[data-column="department"] {
      width: 20%;
    }
    th[data-column="area"],
    th[data-column="value"] {
      width: 15%;
    }
    .observed th[data-column="department"] {
      width: 15%;
    }
    .observed th[data-column="count"] {
      width: 12%;
    }
    .observed th[data-column="value"] {
      width: 13%;
    }
    .observed th[data-column="area"] {
      width: 10%;
    }
  }
  @container (max-width: 480px) {
    [data-column="department"] {
      display: none;
    }
    .place-department {
      display: block;
    }
    th,
    td {
      padding: 10px 5px;
      font-size: 11px;
    }
    th button {
      font-size: 10px;
    }
    th[data-column="id"] {
      width: 23%;
    }
    th[data-column="municipality"] {
      width: 37%;
    }
    th[data-column="area"],
    th[data-column="value"] {
      width: 20%;
    }
    .observed th[data-column="id"] {
      width: 22%;
    }
    .observed th[data-column="municipality"] {
      width: 29%;
    }
    .observed th[data-column="area"] {
      width: 17%;
    }
    .observed th[data-column="value"] {
      width: 18%;
    }
    .observed th[data-column="count"] {
      width: 14%;
    }
    .text-button {
      font-size: 10px;
    }
    .cell-sort {
      width: 100%;
    }
  }
</style>
