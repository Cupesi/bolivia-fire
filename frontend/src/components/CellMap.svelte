<script lang="ts">
  import { untrack } from "svelte";
  import { colorForValue, formatValue } from "../lib/color";
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
    onselect,
  }: {
    grid: Grid;
    boundaries: Boundaries[];
    values: Map<number, NullableNumber>;
    kind: LayerKind;
    selected: Cell | null;
    onselect: (cell: Cell) => void;
  } = $props();
  let canvas: HTMLCanvasElement;
  let width = $state(700);
  let height = $state(560);
  let view = $state<View>({ scale: 1, x: 0, y: 0 });
  let hovered = $state<Cell | null>(null);
  let cursor = $state<Cell | null>(null);
  let focused = $state(false);
  let drag: {
    x: number;
    y: number;
    startX: number;
    startY: number;
    moved: boolean;
  } | null = null;
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
      context.fillStyle = colorForValue(values.get(cell.index), kind);
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
    [...boundaries].reverse().forEach((collection, index) => {
      context.beginPath();
      collection.features.forEach((feature) => {
        if (feature.geometry) traceGeometry(context, feature.geometry);
      });
      context.strokeStyle =
        index === boundaries.length - 1 ? "#294541" : "#607b77";
      context.lineWidth = index === boundaries.length - 1 ? 1.3 : 0.65;
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
    bind:clientWidth={width}
    bind:clientHeight={height}
    role="application"
    tabindex="0"
    aria-label="Bolivia cell map"
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
    <div class="map-controls" aria-label="Map controls">
      <button aria-label="Zoom in" onclick={() => zoom(1.4)}>+</button>
      <button aria-label="Zoom out" onclick={() => zoom(1 / 1.4)}>−</button>
      <button aria-label="Reset map extent" onclick={reset}>Reset</button>
    </div>
    <span class="north-marker" aria-hidden="true">N ↑</span>
    <div class="map-readout" aria-hidden="true">
      {#if hovered}{hovered.id} · {hovered.department} · {formatValue(
          values.get(hovered.index),
          kind,
        )}{:else}Bolivia · {grid.resolution}° grid{/if}
    </div>
  </div>
  <p id="map-help" class="map-help">
    Scroll or +/− to zoom; drag to pan. Arrow keys move between cells, Enter
    selects. Ctrl + arrows pan; Home resets.
  </p>
  <span id="map-cursor" class="sr-only" aria-live="polite"
    >{cursor
      ? `${cursor.id}, ${cursor.department}, ${formatValue(values.get(cursor.index), kind)}`
      : "Press Enter to select a central cell."}</span
  >
</div>
