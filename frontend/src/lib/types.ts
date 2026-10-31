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
  province?: string;
  municipality?: string;
  nearestPlace?: { name: string; distanceKm: number | null };
  populatedPlaces?: PopulatedPlace[];
}
export interface PopulatedPlace {
  name: string;
  geonames_id: number;
  population: number;
  feature_code: string;
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
  modelId: string;
  modelHash: string;
  calibratorHash: string;
  finalTestOpened: string;
  snapshots: Record<string, unknown>;
  layers: string[];
  models: Record<string, string[]>;
  variants: string[];
  limitations: string[];
  attributions: {
    name: string;
    url: string;
    license: string;
    licenseUrl: string;
    product: string;
    provider: string;
    citation: string;
    processing: string;
  }[];
}
export interface Week {
  date: string;
  completeness: NullableNumber;
  targetComplete: boolean | null;
  outage: boolean;
  lowCoverage: boolean;
  observation?: boolean;
}
export interface Layer {
  model: string | null;
  variant: string;
  cell_index: number[];
  value: NullableNumber[];
  n_primary?: NullableNumber[];
  forecast?: Forecast;
  observation?: Observation;
}
export interface LiveIssue {
  issue_date: string;
  forecast: boolean;
  observation: boolean;
  verified: boolean;
}
export interface LiveIndex {
  issues: LiveIssue[];
  latest_issue: string | null;
  last_updated_utc: string;
  limitations?: string[];
  synthetic?: boolean;
}
export interface Forecast {
  issue_date: string;
  target_start: string;
  target_end: string;
  model_id: string;
  model_name?: string;
  variant: string;
  generated_utc: string;
  prospective: boolean;
  publication_lag_hours: number;
  input_window: {
    first_day: string;
    last_day: string;
    unobserved_days: string[];
  };
  cell_index: number[];
  p: NullableNumber[];
}
export interface Observation {
  issue_date: string;
  complete: boolean;
  unobserved_days: string[];
  cell_index: number[];
  y: NullableNumber[];
  n: NullableNumber[];
  coverage?: {
    lastObservedDay: string | null;
    pendingDays: string[];
    unobservedDays: string[];
  };
}
export interface TrackRecord {
  issue_date: string;
  prospective: boolean;
  publication_lag_hours: number;
  generated_utc: string;
  n_cells: number;
  positives: number;
  ap: NullableNumber;
  brier: NullableNumber;
  roc_auc: NullableNumber;
  recall_area_5: NullableNumber;
  recall_area_10: NullableNumber;
  ece: NullableNumber;
  climatology_ap: NullableNumber;
  notes: string;
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
