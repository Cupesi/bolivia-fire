export interface GridBounds {
  rowMin: number;
  rowMax: number;
  colMin: number;
  colMax: number;
}
export interface View {
  scale: number;
  x: number;
  y: number;
}

export function fitGrid(
  bounds: GridBounds,
  width: number,
  height: number,
  padding = 28,
): View {
  const cols = bounds.colMax - bounds.colMin + 1;
  const rows = bounds.rowMax - bounds.rowMin + 1;
  const scale = Math.max(
    0.01,
    Math.min((width - padding * 2) / cols, (height - padding * 2) / rows),
  );
  return {
    scale,
    x: (width - cols * scale) / 2 - bounds.colMin * scale,
    y: (height - rows * scale) / 2 + (bounds.rowMax + 1) * scale,
  };
}

export function screenPoint(
  col: number,
  row: number,
  view: View,
): { x: number; y: number } {
  return { x: view.x + col * view.scale, y: view.y - row * view.scale };
}

export function gridPoint(
  x: number,
  y: number,
  view: View,
): { col: number; row: number } {
  return { col: (x - view.x) / view.scale, row: (view.y - y) / view.scale };
}

export function cellAtPoint(
  x: number,
  y: number,
  view: View,
): { col: number; row: number } {
  const point = gridPoint(x, y, view);
  return {
    col: Math.floor(Math.round(point.col * 1e9) / 1e9),
    row: Math.floor(Math.round(point.row * 1e9) / 1e9),
  };
}

export function zoomAt(view: View, factor: number, x: number, y: number): View {
  return {
    scale: view.scale * factor,
    x: x - (x - view.x) * factor,
    y: y - (y - view.y) * factor,
  };
}
