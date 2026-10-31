import type { GridBounds } from "./projection";

export type LayerKind = "observed" | "estimate";
export type NullableNumber = number | null;
export interface Cell {
  id: string;
  index: number;
  row: number;
  col: number;
  department: string;
  area: number;
}
export interface Grid {
  origin: [number, number];
  resolution: number;
  bounds: GridBounds;
  cells: Cell[];
}
export interface Meta {
  name: string;
  start: string;
  end: string;
  protocolVersion: string;
  protocolHash: string;
  snapshots: Record<string, unknown>;
  layers: string[];
  models: Record<string, string[]>;
  variants: string[];
  limitations: string[];
  attributions: { name: string; url: string; license: string }[];
}
export interface Week {
  date: string;
  completeness: NullableNumber;
  targetComplete: boolean | null;
  outage: boolean;
  lowCoverage: boolean;
}
export interface Layer {
  model: string | null;
  variant: string;
  cell_index: number[];
  value: NullableNumber[];
  n_primary?: NullableNumber[];
}
export interface SeriesPoint {
  date: string;
  y: NullableNumber;
  n: NullableNumber;
  p: NullableNumber;
}
export interface Geometry {
  type: string;
  coordinates?: unknown;
  geometries?: Geometry[];
}
export interface Boundaries {
  type: string;
  features: { geometry: Geometry | null }[];
}
