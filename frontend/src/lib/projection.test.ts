import { describe, expect, it } from "vitest";
import {
  cellAtPoint,
  fitGrid,
  gridPoint,
  screenPoint,
  zoomAt,
} from "./projection";

// Synthetic grid uses south-to-north row indices and west-to-east columns.
const bounds = { rowMin: 10, rowMax: 13, colMin: 20, colMax: 23 };

describe("grid projection", () => {
  it("fits a grid with square cell blocks and centered padding", () => {
    const view = fitGrid(bounds, 400, 300, 20);
    expect(view.scale).toBe(65);
    expect(screenPoint(20, 14, view)).toEqual({ x: 70, y: 20 });
    expect(screenPoint(24, 10, view)).toEqual({ x: 330, y: 280 });
  });
  it("round trips arbitrary points while preserving north-up orientation", () => {
    const view = fitGrid(bounds, 400, 300, 20);
    const screen = screenPoint(21.25, 11.75, view);
    expect(gridPoint(screen.x, screen.y, view)).toEqual({
      col: 21.25,
      row: 11.75,
    });
    expect(cellAtPoint(screen.x, screen.y, view)).toEqual({ col: 21, row: 11 });
  });
  it("uses west/south inclusive and east/north exclusive membership", () => {
    const view = fitGrid(bounds, 400, 300, 20);
    const edge = screenPoint(21, 12, view);
    expect(cellAtPoint(edge.x, edge.y, view)).toEqual({ col: 21, row: 12 });
  });
  it("zooms around the pointer without moving its geographic location", () => {
    const view = fitGrid(bounds, 400, 300, 20);
    const before = gridPoint(120, 80, view);
    const after = gridPoint(120, 80, zoomAt(view, 2, 120, 80));
    expect(after.col).toBeCloseTo(before.col);
    expect(after.row).toBeCloseTo(before.row);
  });
});
