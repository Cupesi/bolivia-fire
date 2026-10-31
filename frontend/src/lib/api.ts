import { en } from "./messages/en";
import type {
  Boundaries,
  Grid,
  Layer,
  LayerKind,
  Meta,
  SeriesPoint,
  Week,
} from "./types";

type JsonObject = Record<string, unknown>;
const object = (value: unknown): JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
const string = (value: unknown): string =>
  typeof value === "string" ? value : "";
const numberOrNull = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const array = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];
const strings = (value: unknown): string[] =>
  array(value).map(string).filter(Boolean);

export class ApiError extends Error {
  constructor(
    public kind: "offline" | "missing" | "filters" | "server" | "invalid",
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function getJson<T = unknown>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      signal,
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("offline", 0, en.apiOfflineTheLocalDataService);
  }
  if (!response.ok) {
    if (response.status === 404)
      throw new ApiError("missing", 404, en.noPreparedDataAreAvailableFor);
    if ([400, 409, 422].includes(response.status))
      throw new ApiError(
        "filters",
        response.status,
        en.incompatibleFiltersChooseAnAvailableWeek,
      );
    throw new ApiError(
      "server",
      response.status,
      en.theDataServiceCouldNotComplete,
    );
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(
      "invalid",
      response.status,
      en.theDataServiceReturnedAnUnreadable,
    );
  }
}

export function normalizeMeta(payload: unknown): Meta {
  const raw = object(payload);
  const protocol = object(raw.protocol);
  const dates = object(raw.issue_date_range);
  const attributions = array(raw.source_attributions).map((value) => {
    const source = object(value);
    return {
      name: string(source.name),
      url: string(source.url),
      license: string(source.license),
      licenseUrl: string(source.license_url),
      product: string(source.product),
      provider: string(source.provider),
      citation: string(source.citation),
      processing: string(source.processing),
    };
  });
  return {
    name: string(raw.project_name),
    start: string(dates.start),
    end: string(dates.end),
    protocolVersion: string(protocol.version),
    protocolHash: string(protocol.sha256),
    modelId: string(raw.model_id),
    modelHash: string(raw.source_model_sha256),
    calibratorHash: string(raw.source_calibrator_sha256),
    finalTestOpened: string(raw.primary_opening_utc),
    snapshots: object(raw.dataset_snapshot_ids),
    layers: strings(raw.available_layers),
    models: Object.fromEntries(
      Object.entries(object(raw.models)).map(([variant, models]) => [
        variant,
        strings(models),
      ]),
    ),
    variants: strings(raw.variants),
    limitations:
      typeof raw.limitations === "string"
        ? [raw.limitations]
        : strings(raw.limitations),
    attributions,
  };
}

export function normalizeGrid(payload: unknown): Grid {
  const raw = object(payload);
  const ids = array(raw.cell_id);
  const rows = array(raw.row);
  const cols = array(raw.col);
  const departments = array(raw.department);
  const areas = array(raw.national_area_km2);
  const placeColumns = [
    "province",
    "municipality",
    "nearest_place_name",
    "nearest_place_distance_km",
    "populated_places",
  ];
  if (
    ![rows, cols, departments, areas].every(
      (column) => column.length === ids.length,
    ) ||
    placeColumns.some(
      (key) =>
        key in raw &&
        (!Array.isArray(raw[key]) || array(raw[key]).length !== ids.length),
    )
  )
    throw new ApiError("invalid", 200, en.invalidGridColumns);
  const cells = ids.map((id, index) => ({
    id: string(id),
    index,
    row: Number(rows[index]),
    col: Number(cols[index]),
    department: string(departments[index]),
    area: Number(areas[index]),
    province: string(array(raw.province)[index]),
    municipality: string(array(raw.municipality)[index]),
    nearestPlace: string(array(raw.nearest_place_name)[index])
      ? {
          name: string(array(raw.nearest_place_name)[index]),
          distanceKm: numberOrNull(array(raw.nearest_place_distance_km)[index]),
        }
      : undefined,
    populatedPlaces: array(array(raw.populated_places)[index])
      .slice(0, 3)
      .map((value) => {
        const place = object(value);
        return {
          name: string(place.name),
          geonames_id: Number(place.geonames_id),
          population: Number(place.population),
          feature_code: string(place.feature_code),
        };
      })
      .filter((place) => Boolean(place.name)),
  }));
  const origin = array(raw.origin);
  const resolution = Number(raw.resolution_deg);
  if (
    origin.length !== 2 ||
    !(resolution > 0) ||
    cells.some(
      (cell) =>
        !Number.isInteger(cell.row) ||
        !Number.isInteger(cell.col) ||
        !cell.id ||
        !Number.isFinite(cell.area),
    )
  )
    throw new ApiError("invalid", 200, en.theGridResponseIsIncomplete);
  const bounds = cells.reduce(
    (bounds, cell) => ({
      rowMin: Math.min(bounds.rowMin, cell.row),
      rowMax: Math.max(bounds.rowMax, cell.row),
      colMin: Math.min(bounds.colMin, cell.col),
      colMax: Math.max(bounds.colMax, cell.col),
    }),
    {
      rowMin: Infinity,
      rowMax: -Infinity,
      colMin: Infinity,
      colMax: -Infinity,
    },
  );
  return {
    cells,
    origin: [Number(origin[0]), Number(origin[1])],
    resolution,
    bounds,
  };
}

export function normalizeWeeks(payload: unknown): Week[] {
  const raw = object(payload);
  return array(raw.weeks)
    .map((value) => {
      const week = object(value);
      return {
        date: string(week.issue_date),
        completeness: numberOrNull(week.label_complete_share),
        targetComplete:
          typeof week.target_complete === "boolean"
            ? week.target_complete
            : null,
        outage: Boolean(week.outage),
        lowCoverage: Boolean(week.target_low_coverage),
      };
    })
    .filter((week) => /^\d{4}-\d{2}-\d{2}$/.test(week.date))
    .sort((a, b) => a.date.localeCompare(b.date));
}

export function normalizeSeries(payload: unknown): SeriesPoint[] {
  const raw = object(payload);
  const dates = array(raw.issue_date);
  return dates.map((date, index) => ({
    date: string(date),
    y: numberOrNull(array(raw.y)[index]),
    n: numberOrNull(array(raw.n_primary)[index]),
    p: numberOrNull(array(raw.p)[index]),
  }));
}

export function filterQuery(
  date: string,
  kind: LayerKind,
  model: string,
  variant: string,
): string {
  const query = new URLSearchParams({ issue_date: date, kind });
  if (model) query.set("model", model);
  if (variant) query.set("variant", variant);
  return query.toString();
}

export function exportUrl(
  date: string,
  kind: LayerKind,
  model: string,
  variant: string,
): string {
  return `/api/v1/export?${filterQuery(date, kind, model, variant)}&format=csv`;
}

export async function loadWeeks(signal?: AbortSignal): Promise<Week[]> {
  const weeks: Week[] = [];
  for (let offset = 0; offset < 700; offset += 100) {
    const payload = await getJson<{ weeks: unknown[]; total: number }>(
      `/weeks?limit=100&offset=${offset}`,
      signal,
    );
    const page = normalizeWeeks(payload);
    weeks.push(...page);
    if (!page.length || weeks.length >= payload.total || page.length < 100)
      break;
  }
  return weeks.sort((a, b) => a.date.localeCompare(b.date));
}

export async function loadInitial(signal?: AbortSignal): Promise<{
  meta: Meta;
  grid: Grid;
  weeks: Week[];
  boundaries: Boundaries[];
}> {
  const [meta, grid, weeks, ...boundaries] = await Promise.all([
    getJson("/meta", signal),
    getJson("/grid", signal),
    loadWeeks(signal),
    getJson<Boundaries>("/boundaries?level=0", signal),
    getJson<Boundaries>("/boundaries?level=1", signal),
    getJson<Boundaries>("/boundaries?level=2", signal),
    getJson<Boundaries>("/boundaries?level=3", signal),
  ]);
  return {
    meta: normalizeMeta(meta),
    grid: normalizeGrid(grid),
    weeks,
    boundaries,
  };
}

export async function loadLayer(
  date: string,
  kind: LayerKind,
  model: string,
  variant: string,
  signal: AbortSignal,
): Promise<Layer> {
  const layer = await getJson<Layer>(
    `/layer?${filterQuery(date, kind, model, variant)}`,
    signal,
  );
  if (
    !Array.isArray(layer.cell_index) ||
    !Array.isArray(layer.value) ||
    layer.cell_index.length !== layer.value.length
  )
    throw new ApiError(
      "invalid",
      200,
      "The layer response has inconsistent columns.",
    );
  return layer;
}
