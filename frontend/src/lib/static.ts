import { ApiError, normalizeGrid, normalizeMeta } from "./api";
import type {
  Boundaries,
  Forecast,
  Layer,
  LayerKind,
  LiveIndex,
  Observation,
  SeriesPoint,
  TrackRecord,
} from "./types";

const datePattern = /^\d{4}-\d{2}-\d{2}$/;
const invalid = (message: string) => new ApiError("invalid", 200, message);

export function parseTrackRecord(csv: string): TrackRecord[] {
  const rows: string[][] = [];
  let row: string[] = [],
    field = "",
    quoted = false;
  for (let i = 0; i < csv.length; i++) {
    const c = csv[i];
    if (c === '"') {
      if (quoted && csv[i + 1] === '"') {
        field += '"';
        i++;
      } else quoted = !quoted;
    } else if (c === "," && !quoted) {
      row.push(field);
      field = "";
    } else if (c === "\n" && !quoted) {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
    } else field += c;
  }
  if (quoted) throw invalid("The track record CSV is unreadable.");
  if (field || row.length) {
    row.push(field.replace(/\r$/, ""));
    rows.push(row);
  }
  const header = rows.shift() ?? [];
  const metrics = [
    "ap",
    "brier",
    "roc_auc",
    "recall_area_5",
    "recall_area_10",
    "ece",
    "climatology_ap",
  ] as const;
  if (
    ![
      "issue_date",
      "n_cells",
      "positives",
      ...metrics,
      "notes",
      "prospective",
      "generated_utc",
      "publication_lag_hours",
    ].every((key) => header.includes(key))
  )
    throw invalid("The track record CSV is missing required columns.");
  return rows
    .filter((row) => row.some(Boolean))
    .map((row) => {
      if (row.length !== header.length)
        throw invalid("The track record CSV has inconsistent columns.");
      const raw = Object.fromEntries(header.map((key, i) => [key, row[i]]));
      if (!datePattern.test(raw.issue_date))
        throw invalid("The track record has an invalid issue date.");
      if (
        !/^(true|false)$/i.test(raw.prospective) ||
        !Number.isFinite(Date.parse(raw.generated_utc))
      )
        throw invalid(
          "The track record is missing forecast timing provenance.",
        );
      const numeric = (key: string) => {
        const value = raw[key].trim();
        if (!value || value === "null") return null;
        const number = Number(value);
        if (!Number.isFinite(number))
          throw invalid("The track record contains an invalid metric.");
        return number;
      };
      const n_cells = numeric("n_cells"),
        positives = numeric("positives"),
        publication_lag_hours = numeric("publication_lag_hours");
      if (n_cells === null || positives === null)
        throw invalid("The track record is missing population counts.");
      if (publication_lag_hours === null || publication_lag_hours < 0)
        throw invalid("The track record is missing publication lag.");
      return {
        issue_date: raw.issue_date,
        n_cells,
        positives,
        notes: raw.notes,
        prospective: raw.prospective.toLowerCase() === "true",
        generated_utc: raw.generated_utc,
        publication_lag_hours,
        ...Object.fromEntries(metrics.map((key) => [key, numeric(key)])),
      } as TrackRecord;
    })
    .sort((a, b) => a.issue_date.localeCompare(b.issue_date));
}

export function createStaticClient(base: string) {
  const root = `${base.replace(/\/$/, "")}/data/`;
  let catalog: LiveIndex | null = null;
  let metadata: Record<string, unknown> | null = null;

  async function text(path: string, signal?: AbortSignal): Promise<string> {
    let response: Response;
    try {
      response = await fetch(root + path, { signal });
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") throw error;
      throw new ApiError(
        "offline",
        0,
        "Data files unavailable. Check your connection and retry.",
      );
    }
    if (!response.ok)
      throw new ApiError(
        response.status === 404 ? "missing" : "server",
        response.status,
        `Data file unavailable: ${path}.`,
      );
    return response.text();
  }
  async function json<T>(path: string, signal?: AbortSignal): Promise<T> {
    const body = await text(path, signal);
    try {
      return JSON.parse(body) as T;
    } catch {
      throw invalid(`Data file is unreadable: ${path}.`);
    }
  }
  async function index(signal?: AbortSignal): Promise<LiveIndex> {
    if (catalog) return catalog;
    const raw = await json<LiveIndex>("index.json", signal);
    if (
      !raw ||
      !Array.isArray(raw.issues) ||
      !Number.isFinite(Date.parse(raw.last_updated_utc)) ||
      raw.issues.some((issue) => !issue || !datePattern.test(issue.issue_date))
    )
      throw invalid("The issue catalog is incomplete.");
    catalog = {
      ...raw,
      issues: [...raw.issues].sort((a, b) =>
        a.issue_date.localeCompare(b.issue_date),
      ),
    };
    return catalog;
  }
  async function loadMetadata(signal?: AbortSignal) {
    metadata ??= await json<Record<string, unknown>>(
      "static/meta.json",
      signal,
    );
    return metadata;
  }
  async function loadInitial(signal?: AbortSignal) {
    const [live, metaRaw, gridRaw, adm0, adm1] = await Promise.all([
      index(signal),
      loadMetadata(signal),
      json("static/grid.json", signal),
      json<Boundaries>("static/boundaries_adm0.json", signal),
      json<Boundaries>("static/boundaries_adm1.json", signal),
    ]);
    const meta = normalizeMeta({
      ...metaRaw,
      protocol: metaRaw.protocol ?? { sha256: metaRaw.protocol_sha256 },
      source_attributions: metaRaw.source_attributions ?? metaRaw.attribution,
      dataset_snapshot_ids:
        metaRaw.dataset_snapshot_ids ??
        Object.fromEntries(
          [
            "protocol_sha256",
            "frozen_sp_bundle_sha256",
            "outage_sha256",
          ].flatMap((key) =>
            typeof metaRaw[key] === "string" ? [[key, metaRaw[key]]] : [],
          ),
        ),
    });
    meta.layers = ["estimate", "observed"];
    meta.limitations = [
      ...new Set([...meta.limitations, ...(live.limitations ?? [])]),
    ];
    return {
      live,
      meta,
      grid: normalizeGrid(gridRaw),
      boundaries: [adm0, adm1],
      weeks: live.issues
        .filter((issue) => issue.forecast)
        .map((issue) => ({
          date: issue.issue_date,
          completeness: null,
          targetComplete: null,
          outage: false,
          lowCoverage: false,
          observation: issue.observation,
        })),
    };
  }
  async function loadLayer(
    date: string,
    kind: LayerKind,
    _model: string,
    _variant: string,
    signal: AbortSignal,
  ): Promise<Layer> {
    if (!datePattern.test(date))
      throw new ApiError("filters", 422, "Choose an available issue week.");
    const raw = await json<Forecast | Observation>(
      `${kind === "estimate" ? "forecasts" : "observations"}/${date}.json`,
      signal,
    );
    if (!raw || typeof raw !== "object")
      throw invalid("The weekly data file is incomplete.");
    const value = "p" in raw ? raw.p : raw.y;
    if (
      raw.issue_date !== date ||
      !Array.isArray(raw.cell_index) ||
      !Array.isArray(value) ||
      value.length !== raw.cell_index.length ||
      new Set(raw.cell_index).size !== raw.cell_index.length ||
      raw.cell_index.some((i) => !Number.isInteger(i) || i < 0) ||
      value.some(
        (v) =>
          v !== null &&
          (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 1),
      )
    )
      throw invalid("The weekly data file has inconsistent columns or values.");
    if ("p" in raw) {
      if (
        typeof raw.prospective !== "boolean" ||
        !Number.isFinite(raw.publication_lag_hours) ||
        raw.publication_lag_hours < 0 ||
        !raw.input_window ||
        !Array.isArray(raw.input_window.unobserved_days) ||
        !Number.isFinite(Date.parse(raw.generated_utc))
      )
        throw invalid(
          "The forecast file is missing input history or timing provenance.",
        );
      return {
        model: raw.model_id,
        variant: raw.variant,
        cell_index: raw.cell_index,
        value,
        forecast: {
          ...raw,
          model_name:
            (await loadMetadata(signal)).model_id === raw.model_id &&
            typeof metadata?.model_name === "string"
              ? metadata.model_name
              : undefined,
        },
      };
    }
    if (
      typeof raw.complete !== "boolean" ||
      !Array.isArray(raw.unobserved_days) ||
      raw.unobserved_days.some((day) => !datePattern.test(day)) ||
      !Array.isArray(raw.n) ||
      raw.n.length !== value.length ||
      (!raw.complete && value.some((v) => v !== null))
    )
      throw invalid(
        "The observation file has inconsistent completeness or counts.",
      );
    const live = await index(signal);
    const snapshotDay = new Date(live.last_updated_utc)
      .toISOString()
      .slice(0, 10);
    const days = Array.from({ length: 7 }, (_, offset) =>
      new Date(Date.parse(date + "T00:00:00Z") + offset * 86400000)
        .toISOString()
        .slice(0, 10),
    );
    const observedDays = days.filter(
      (day) => day < snapshotDay && !raw.unobserved_days.includes(day),
    );
    return {
      model: null,
      variant: "strict",
      cell_index: raw.cell_index,
      value,
      n_primary: raw.n,
      observation: {
        ...raw,
        coverage: {
          lastObservedDay: observedDays.at(-1) ?? null,
          pendingDays: days.filter((day) => day >= snapshotDay),
          unobservedDays: raw.unobserved_days.filter(
            (day) => day < snapshotDay,
          ),
        },
      },
    };
  }
  async function loadSeries(
    cellIndex: number,
    start: string,
    end: string,
    signal: AbortSignal,
  ): Promise<SeriesPoint[]> {
    const live = await index(signal);
    const issues = live.issues.filter(
      (issue) => issue.issue_date >= start && issue.issue_date <= end,
    );
    const points: SeriesPoint[] = [];
    for (let offset = 0; offset < issues.length; offset += 4) {
      points.push(
        ...(await Promise.all(
          issues.slice(offset, offset + 4).map(async (issue) => {
            const [forecast, observation] = await Promise.all([
              issue.forecast
                ? loadLayer(issue.issue_date, "estimate", "", "", signal)
                : null,
              issue.observation
                ? loadLayer(issue.issue_date, "observed", "", "", signal)
                : null,
            ]);
            const pIndex = forecast?.cell_index.indexOf(cellIndex) ?? -1;
            const yIndex = observation?.cell_index.indexOf(cellIndex) ?? -1;
            return {
              date: issue.issue_date,
              p: forecast?.value[pIndex] ?? null,
              y: observation?.value[yIndex] ?? null,
              n: observation?.n_primary?.[yIndex] ?? null,
            };
          }),
        )),
      );
    }
    return points;
  }
  async function loadTrackRecord(signal?: AbortSignal): Promise<TrackRecord[]> {
    return parseTrackRecord(
      await text("verification/track_record.csv", signal),
    );
  }
  return { loadInitial, loadLayer, loadSeries, loadTrackRecord };
}
