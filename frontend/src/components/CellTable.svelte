<script lang="ts">
  import { i18n, message, countMessage, formatNumber } from "../lib/i18n";
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
    { key: "id", label: $i18n.messages.cell, aria: $i18n.messages.cellId },
    {
      key: "municipality",
      label: $i18n.messages.municipality,
      aria: $i18n.messages.municipality2,
    },
    {
      key: "province",
      label: $i18n.messages.province,
      aria: $i18n.messages.province2,
    },
    {
      key: "department",
      label: $i18n.messages.department,
      aria: $i18n.messages.department2,
    },
    {
      key: "nearestPlace",
      label: $i18n.messages.nearestPlace,
      aria: $i18n.messages.nearestPlace2,
    },
    {
      key: "area",
      label: $i18n.messages.areaKm,
      aria: $i18n.messages.nationalArea2,
    },
    {
      key: "value",
      label:
        kind === "estimate"
          ? $i18n.messages.probability
          : $i18n.messages.detected,
      aria: $i18n.messages.value,
    },
    ...(kind === "observed"
      ? [
          {
            key: "count",
            label: $i18n.messages.count,
            aria: $i18n.messages.count2,
          },
        ]
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
      >{$i18n.messages.filterPlaces}<input
        id="place-filter"
        type="search"
        bind:value={query}
        placeholder={$i18n.messages.municipalityProvinceDepartmentOrPlace}
        oninput={() => {
          page = 0;
        }}
      /></label
    >
    <label for="top-cells"
      >{$i18n.messages.topCells}<select
        id="top-cells"
        bind:value={top}
        onchange={() => {
          page = 0;
        }}
        ><option value="50">{$i18n.messages.top50ByValue}</option><option
          value="100">{$i18n.messages.top100ByValue}</option
        ><option value="500">{$i18n.messages.top500ByValue}</option><option
          value="all">{$i18n.messages.allCells}</option
        ></select
      ></label
    >
    <div class="cell-sort">
      <label for="cell-sort"
        >{$i18n.messages.sortCellsBy}<select
          id="cell-sort"
          value={sort}
          onchange={(event) =>
            changeSort(event.currentTarget.value as CellSort)}
          >{#each columns as column}<option value={column.key}
              >{column.label}</option
            >{/each}</select
        ></label
      ><button
        aria-label={$i18n.messages.reverseCellSort}
        onclick={() => {
          descending = !descending;
          page = 0;
        }}
        >{descending
          ? $i18n.messages.descending
          : $i18n.messages.ascending}</button
      >
    </div>
    <p class="muted small" id="cell-table-hint">
      {$i18n.messages.filterBeforeRankingMissingValuesSort}
    </p>
  </div>
  <table
    aria-label={$i18n.messages.currentWeekCells}
    aria-describedby="cell-table-hint"
  >
    <caption class="sr-only">{$i18n.messages.tableCaption}</caption>
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
              aria-label={message($i18n, "sortByColumn", {
                column: column.aria,
              })}>{column.label}</button
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
              aria-label={message($i18n, "selectCellId", { id: cell.id })}
              aria-pressed={selected?.id === cell.id}
              onclick={() => onselect(cell)}
              onkeydown={(event) => rowKeyboard(event, index)}>{cell.id}</button
            ></td
          >
          <td data-column="municipality"
            >{cell.municipality || $i18n.messages.unassigned}
            <div class="place-secondary">
              <span>{cell.province || $i18n.messages.unassigned}</span><span
                >{nearestPlaceLabel(cell, $i18n)}</span
              >
            </div>
            <span class="place-department"
              >{cell.department || $i18n.messages.unassigned}</span
            ></td
          >
          <td data-column="province"
            >{cell.province || $i18n.messages.unassigned}</td
          >
          <td data-column="department"
            >{cell.department || $i18n.messages.unassigned}</td
          >
          <td data-column="nearestPlace">{nearestPlaceLabel(cell, $i18n)}</td>
          <td data-column="area" class="numeric"
            >{formatNumber(cell.area, $i18n.language, 2)}</td
          >
          <td data-column="value" class="numeric"
            >{values.get(cell.index) == null
              ? $i18n.messages.missing
              : kind === "estimate"
                ? formatNumber(values.get(cell.index)!, $i18n.language, 3)
                : formatNumber(values.get(cell.index)!, $i18n.language)}</td
          >
          {#if kind === "observed"}<td data-column="count" class="numeric"
              >{counts.get(cell.index) == null
                ? $i18n.messages.missing
                : formatNumber(counts.get(cell.index)!, $i18n.language)}</td
            >{/if}
        </tr>{/each}</tbody
    >
  </table>
  {#if !visible.length}<p class="empty-state" role="status">
      {query.trim()
        ? $i18n.messages.noCellsMatchThisPlaceFilter
        : $i18n.messages.noCellsAreAvailableForThis}
    </p>{/if}
  <div class="pagination">
    <button
      aria-label={$i18n.messages.previousTablePage}
      disabled={safePage === 0}
      onclick={() => {
        page = safePage - 1;
      }}>{$i18n.messages.previous}</button
    ><span data-testid="table-page" role="status"
      >{countMessage($i18n, "tablePage", sorted.length, {
        page: formatNumber(safePage + 1, $i18n.language),
        pages: formatNumber(pageCount, $i18n.language),
      })}</span
    ><button
      aria-label={$i18n.messages.nextTablePage}
      disabled={safePage >= pageCount - 1}
      onclick={() => {
        page = safePage + 1;
      }}>{$i18n.messages.next}</button
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
