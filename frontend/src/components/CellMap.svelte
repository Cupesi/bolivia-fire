<script lang="ts">
  import { i18n, formatNumber } from "../lib/i18n";
  import { untrack } from "svelte";
  import { colorForValue, formatValue } from "../lib/color";
  import CellPlaces from "./CellPlaces.svelte";
  import { boundaryLevels, placeLabels } from "../lib/map-detail";
  import {
    cellAtPoint,
    fitGrid,
    screenPoint,
    zoomAt,
    type View,
  } from "../lib/projection";
  import type {
    Boundaries,
    Cell,
    Geometry,
    Grid,
    LayerKind,
    NullableNumber,
  } from "../lib/types";

  let {
    grid,
    boundaries,
    values,
    kind,
    selected,
    showHoldout = false,
    onselect,
  }: {
    grid: Grid;
    boundaries: Boundaries[];
    values: Map<number, NullableNumber>;
    kind: LayerKind;
    selected: Cell | null;
    showHoldout?: boolean;
    onselect: (cell: Cell) => void;
  } = $props();
  let canvas: HTMLCanvasElement;
  let width = $state(700);
  let height = $state(560);
  let view = $state<View>({ scale: 1, x: 0, y: 0 });
  let hovered = $state<Cell | null>(null);
  let cursor = $state<Cell | null>(null);
  let focused = $state(false);
  let pointer = $state({ x: 0, y: 0 });
  let tooltipHeight = $state(200);
  const zoomLevel = $derived(
    view.scale / fitGrid(grid.bounds, width, height).scale,
  );
  const visibleLevels = $derived(boundaryLevels(zoomLevel));
  const labels = $derived.by(() => {
    if (zoomLevel < 2 || !canvas) return [];
    const context = canvas.getContext("2d")!;
    context.font = "600 11px system-ui";
    return placeLabels(
      grid,
      view,
      width,
      height,
      (name) => context.measureText(name).width,
    );
  });
  let drag = $state<{
    x: number;
    y: number;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  const byPosition = $derived(
    new Map(grid.cells.map((cell) => [`${cell.row}:${cell.col}`, cell])),
  );
  const centerCell = $derived.by(() => {
    const row = (grid.bounds.rowMax + grid.bounds.rowMin) / 2;
    const col = (grid.bounds.colMax + grid.bounds.colMin) / 2;
    return grid.cells.reduce<Cell | null>(
      (closest, cell) =>
        !closest ||
        (cell.row - row) ** 2 + (cell.col - col) ** 2 <
          (closest.row - row) ** 2 + (closest.col - col) ** 2
          ? cell
          : closest,
      null,
    );
  });
  const raster = $derived.by(() => {
    const image = document.createElement("canvas");
    image.width = grid.bounds.colMax - grid.bounds.colMin + 1;
    image.height = grid.bounds.rowMax - grid.bounds.rowMin + 1;
    const context = image.getContext("2d")!;
    for (const cell of grid.cells) {
      context.fillStyle =
        showHoldout && cell.geoHoldout && values.get(cell.index) == null
          ? "#e9d8ee"
          : colorForValue(values.get(cell.index), kind);
      context.fillRect(
        cell.col - grid.bounds.colMin,
        grid.bounds.rowMax - cell.row,
        1,
        1,
      );
    }
    return image;
  });

  $effect(() => {
    const fitted = fitGrid(grid.bounds, width, height);
    untrack(() => {
      view = fitted;
    });
  });

  function traceGeometry(
    context: CanvasRenderingContext2D,
    geometry: Geometry,
  ) {
    if (geometry.type === "GeometryCollection") {
      geometry.geometries?.forEach((child) => traceGeometry(context, child));
      return;
    }
    const polygon = (rings: number[][][]) => {
      for (const ring of rings) {
        ring.forEach(([longitude, latitude], index) => {
          const point = screenPoint(
            (longitude - grid.origin[0]) / grid.resolution,
            (latitude - grid.origin[1]) / grid.resolution,
            view,
          );
          if (index === 0) context.moveTo(point.x, point.y);
          else context.lineTo(point.x, point.y);
        });
        context.closePath();
      }
    };
    if (geometry.type === "Polygon")
      polygon(geometry.coordinates as number[][][]);
    if (geometry.type === "MultiPolygon")
      (geometry.coordinates as number[][][][]).forEach(polygon);
  }

  $effect(() => {
    if (!canvas || width <= 0 || height <= 0) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const context = canvas.getContext("2d")!;
    context.scale(ratio, ratio);
    context.fillStyle = "#eaf0f0";
    context.fillRect(0, 0, width, height);
    context.imageSmoothingEnabled = false;
    const topLeft = screenPoint(
      grid.bounds.colMin,
      grid.bounds.rowMax + 1,
      view,
    );
    context.drawImage(
      raster,
      topLeft.x,
      topLeft.y,
      raster.width * view.scale,
      raster.height * view.scale,
    );
    if (showHoldout) {
      context.beginPath();
      for (const cell of grid.cells) {
        if (!cell.geoHoldout) continue;
        for (const [dr, dc, x1, y1, x2, y2] of [
          [0, -1, 0, 0, 0, 1],
          [0, 1, 1, 0, 1, 1],
          [-1, 0, 0, 0, 1, 0],
          [1, 0, 0, 1, 1, 1],
        ]) {
          if (byPosition.get(`${cell.row + dr}:${cell.col + dc}`)?.geoHoldout)
            continue;
          const from = screenPoint(cell.col + x1, cell.row + y1, view);
          const to = screenPoint(cell.col + x2, cell.row + y2, view);
          context.moveTo(from.x, from.y);
          context.lineTo(to.x, to.y);
        }
      }
      context.strokeStyle = "#a23e92";
      context.lineWidth = 1;
      context.stroke();
    }
    [...visibleLevels].reverse().forEach((level) => {
      const collection = boundaries[level];
      if (!collection) return;
      context.beginPath();
      collection.features.forEach((feature) => {
        if (feature.geometry) traceGeometry(context, feature.geometry);
      });
      context.strokeStyle = ["#294541", "#607b77", "#7c8d8999", "#89979270"][
        level
      ];
      context.lineWidth = [1.3, 0.75, 0.6, 0.45][level];
      context.stroke();
    });
    for (const [cell, color] of [
      [selected, "#ffffff"],
      [focused ? cursor : hovered, "#153d39"],
    ] as const) {
      if (!cell) continue;
      const point = screenPoint(cell.col, cell.row + 1, view);
      const size = Math.max(view.scale, 5);
      context.strokeStyle = "#ffffff";
      context.lineWidth = 4;
      context.strokeRect(
        point.x - (size - view.scale) / 2,
        point.y - (size - view.scale) / 2,
        size,
        size,
      );
      context.strokeStyle = color === "#ffffff" ? "#182e2c" : color;
      context.lineWidth = 2;
      context.strokeRect(
        point.x - (size - view.scale) / 2,
        point.y - (size - view.scale) / 2,
        size,
        size,
      );
    }
  });

  function reset() {
    view = fitGrid(grid.bounds, width, height);
    cursor = centerCell;
  }
  function zoom(factor: number, x = width / 2, y = height / 2) {
    const fitted = fitGrid(grid.bounds, width, height);
    const scale = Math.max(
      fitted.scale * 0.75,
      Math.min(fitted.scale * 40, view.scale * factor),
    );
    view = zoomAt(view, scale / view.scale, x, y);
  }
  function local(event: MouseEvent | PointerEvent | WheelEvent) {
    const bounds = canvas.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  }
  function cellFor(x: number, y: number) {
    const point = cellAtPoint(x, y, view);
    return byPosition.get(`${point.row}:${point.col}`) ?? null;
  }
  function pointerDown(event: PointerEvent) {
    if (event.button !== 0) return;
    const point = local(event);
    drag = { ...point, startX: point.x, startY: point.y, moved: false };
    canvas.setPointerCapture(event.pointerId);
    canvas.parentElement?.focus({ preventScroll: true });
  }
  function pointerMove(event: PointerEvent) {
    const point = local(event);
    pointer = point;
    if (drag) {
      if (Math.hypot(point.x - drag.startX, point.y - drag.startY) > 4)
        drag.moved = true;
      if (drag.moved)
        view = {
          ...view,
          x: view.x + point.x - drag.x,
          y: view.y + point.y - drag.y,
        };
      drag.x = point.x;
      drag.y = point.y;
    }
    hovered = cellFor(point.x, point.y);
  }
  function pointerUp(event: PointerEvent) {
    const point = local(event);
    const cell = cellFor(point.x, point.y);
    if (drag && !drag.moved && cell) {
      cursor = cell;
      onselect(cell);
    }
    drag = null;
    if (canvas.hasPointerCapture(event.pointerId))
      canvas.releasePointerCapture(event.pointerId);
  }
  function wheel(node: HTMLCanvasElement) {
    const handler = (event: WheelEvent) => {
      event.preventDefault();
      const point = local(event);
      zoom(Math.exp(-event.deltaY * 0.0015), point.x, point.y);
    };
    node.addEventListener("wheel", handler, { passive: false });
    return { destroy: () => node.removeEventListener("wheel", handler) };
  }
  function keydown(event: KeyboardEvent) {
    if (event.target !== event.currentTarget) return;
    if (event.altKey) return;
    const direction: Record<string, [number, number]> = {
      ArrowLeft: [0, -1],
      ArrowRight: [0, 1],
      ArrowUp: [1, 0],
      ArrowDown: [-1, 0],
    };
    if (direction[event.key]) {
      event.preventDefault();
      const [dr, dc] = direction[event.key];
      if (event.ctrlKey || event.metaKey) {
        view = { ...view, x: view.x - dc * 40, y: view.y + dr * 40 };
        return;
      }
      const current = cursor ?? selected ?? centerCell;
      if (!current) return;
      cursor = current;
      for (
        let step = 1;
        step <=
        Math.max(
          grid.bounds.colMax - grid.bounds.colMin,
          grid.bounds.rowMax - grid.bounds.rowMin,
        );
        step++
      ) {
        const candidate = byPosition.get(
          `${current.row + dr * step}:${current.col + dc * step}`,
        );
        if (candidate) {
          cursor = candidate;
          break;
        }
      }
      const point = screenPoint(cursor.col + 0.5, cursor.row + 0.5, view);
      view = {
        ...view,
        x:
          view.x +
          Math.max(20 - point.x, 0) -
          Math.max(point.x - width + 20, 0),
        y:
          view.y +
          Math.max(20 - point.y, 0) -
          Math.max(point.y - height + 20, 0),
      };
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const cell = cursor ?? selected ?? centerCell;
      if (cell) {
        cursor = cell;
        onselect(cell);
      }
    } else if (["+", "="].includes(event.key)) {
      event.preventDefault();
      zoom(1.4);
    } else if (event.key === "-") {
      event.preventDefault();
      zoom(1 / 1.4);
    } else if (event.key === "Home") {
      event.preventDefault();
      reset();
    }
  }
</script>

<div class="map-shell">
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions (The application owns spatial keyboard interaction; the table provides the semantic alternative.) -->
  <div
    class="map-canvas"
    data-zoom={zoomLevel.toFixed(3)}
    data-boundary-levels={visibleLevels.join(",")}
    data-holdout={showHoldout}
    bind:clientWidth={width}
    bind:clientHeight={height}
    role="application"
    tabindex="0"
    aria-label={$i18n.messages.boliviaCellMap}
    aria-describedby="map-help map-cursor"
    onkeydown={keydown}
    onfocus={() => {
      focused = true;
      cursor ??= selected ?? centerCell;
    }}
    onblur={() => {
      focused = false;
    }}
  >
    <canvas
      bind:this={canvas}
      use:wheel
      aria-hidden="true"
      onpointerdown={pointerDown}
      onpointermove={pointerMove}
      onpointerup={pointerUp}
      onpointercancel={() => {
        drag = null;
      }}
      onpointerleave={() => {
        hovered = null;
      }}
    ></canvas>
    {#each labels as label (label.geonames_id)}
      <span
        class="town-label"
        aria-hidden="true"
        style:left="{label.x}px"
        style:top="{label.y}px"
        style:width="{label.width}px">{label.name}</span
      >
    {/each}
    {#if hovered && !drag}
      <div
        class="place-tooltip"
        role="tooltip"
        bind:clientHeight={tooltipHeight}
        style:left="{Math.max(8, Math.min(pointer.x + 14, width - 292))}px"
        style:top="{Math.max(
          52,
          Math.min(pointer.y + 14, height - tooltipHeight - 8),
        )}px"
      >
        <strong>{hovered.id}</strong> · {formatValue(
          values.get(hovered.index),
          kind,
          $i18n,
        )}
        <CellPlaces cell={hovered} />
      </div>
    {/if}
    <div class="map-controls" aria-label={$i18n.messages.mapControls}>
      <button aria-label={$i18n.messages.zoomIn} onclick={() => zoom(1.4)}
        >+</button
      >
      <button aria-label={$i18n.messages.zoomOut} onclick={() => zoom(1 / 1.4)}
        >−</button
      >
      <button aria-label={$i18n.messages.resetMapExtent} onclick={reset}
        >{$i18n.messages.reset}</button
      >
    </div>
    <span class="north-marker" aria-hidden="true">{$i18n.messages.n}</span>
    <div class="map-readout" aria-hidden="true">
      {#if hovered}{hovered.id} · {hovered.department} · {formatValue(
          values.get(hovered.index),
          kind,
          $i18n,
        )}{:else}{$i18n.messages.bolivia}
        {formatNumber(grid.resolution, $i18n.language, 2)}{$i18n.messages
          .grid}{/if}
    </div>
  </div>
  <p id="map-help" class="map-help">
    {$i18n.messages.mapHelp}
  </p>
  <span id="map-cursor" class="sr-only" aria-live="polite"
    >{cursor
      ? `${cursor.id}, ${cursor.department}, ${formatValue(values.get(cursor.index), kind, $i18n)}`
      : $i18n.messages.pressEnterToSelectACentral}</span
  >
</div>

<style>
  .town-label {
    position: absolute;
    pointer-events: none;
    height: 18px;
    padding: 0 4px;
    box-sizing: border-box;
    white-space: nowrap;
    font: 600 11px/18px system-ui;
    color: #173c38;
    background: #ffffffe6;
    border-radius: 2px;
    box-shadow: 0 0 0 1px #173c381a;
  }
  .place-tooltip {
    position: absolute;
    z-index: 2;
    pointer-events: none;
    width: min(284px, calc(100% - 16px));
    box-sizing: border-box;
    padding: 10px 12px;
    color: #173c38;
    background: #fffffff5;
    border: 1px solid #afc3be;
    border-radius: 4px;
    box-shadow: 0 2px 8px #173c3826;
    font-size: 0.8rem;
  }
</style>
