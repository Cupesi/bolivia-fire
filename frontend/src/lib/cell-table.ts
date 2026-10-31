import type { Cell, Layer, LayerKind, Meta, NullableNumber } from "./types";
import { englishState, formatDistance, type LanguageState } from "./i18n";

export type CellSort =
  | "id"
  | "municipality"
  | "province"
  | "department"
  | "nearestPlace"
  | "area"
  | "value"
  | "count";
const searchable = (text: string) =>
  text.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase();

export function nearestPlaceLabel(
  cell: Cell,
  state: LanguageState = englishState,
): string {
  const place = cell.nearestPlace;
  if (!place) return state.messages.unassigned;
  const distance = place.distanceKm;
  return `${place.name}${distance === null ? "" : ` · ${formatDistance(distance, state.language)}`}`;
}

export function tableCells(
  cells: Cell[],
  values: Map<number, NullableNumber>,
  counts: Map<number, NullableNumber>,
  query: string,
  top: string,
  sort: CellSort,
  descending: boolean,
): Cell[] {
  const needle = searchable(query.trim());
  const ranked = cells
    .filter(
      (cell) =>
        !needle ||
        [
          cell.municipality,
          cell.province,
          cell.department,
          cell.nearestPlace?.name,
        ].some((name) => name && searchable(name).includes(needle)),
    )
    .sort(
      (a, b) =>
        (values.get(b.index) ?? -Infinity) -
          (values.get(a.index) ?? -Infinity) || a.id.localeCompare(b.id),
    )
    .slice(0, top === "all" ? undefined : Number(top));
  const value = (cell: Cell) =>
    sort === "value"
      ? values.get(cell.index)
      : sort === "count"
        ? counts.get(cell.index)
        : sort === "nearestPlace"
          ? cell.nearestPlace?.name
          : cell[sort];
  return ranked.sort((a, b) => {
    const first = value(a),
      second = value(b);
    const missingFirst = first == null || first === "",
      missingSecond = second == null || second === "";
    if (missingFirst || missingSecond)
      return missingFirst ? (missingSecond ? a.id.localeCompare(b.id) : 1) : -1;
    const difference =
      typeof first === "number" && typeof second === "number"
        ? first - second
        : String(first).localeCompare(String(second));
    const distance =
      sort === "nearestPlace" && !difference
        ? (a.nearestPlace?.distanceKm ?? Infinity) -
          (b.nearestPlace?.distanceKm ?? Infinity)
        : 0;
    return (
      (difference || distance) * (descending ? -1 : 1) ||
      a.id.localeCompare(b.id)
    );
  });
}

export function weekCsv(
  cells: Cell[],
  layer: Layer,
  date: string,
  kind: LayerKind,
  meta: Meta,
): string {
  const escape = (value: unknown) => {
    const text = value == null ? "" : String(value);
    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  const comments = [
    `# project=Bolivia Fire; protocol_sha256=${meta.protocolHash}`,
    `# issue_date=${date}; kind=${kind}; variant=${layer.variant}; model=${layer.model ?? ""}`,
    `# dataset_snapshot_ids=${JSON.stringify(meta.snapshots)}`,
    ...meta.attributions.map(
      (source) => `# Source: ${Object.values(source).join("; ")}`,
    ),
    ...(layer.forecast
      ? [
          `# forecast=${JSON.stringify(layer.forecast, (key, value) => (key === "cell_index" || key === "p" ? undefined : value))}`,
        ]
      : []),
    "# Detections are not fires; zeros are not proof of absence; missing labels are empty.",
  ];
  const byIndex = new Map(cells.map((cell) => [cell.index, cell]));
  const rows = layer.cell_index.map((index, offset) => {
    const cell = byIndex.get(index)!;
    const value = layer.value[offset];
    return [
      cell.id,
      date,
      kind,
      layer.variant,
      layer.model,
      value,
      layer.n_primary?.[offset],
      cell.municipality,
      cell.province,
      cell.department,
      cell.nearestPlace?.name,
      cell.nearestPlace?.distanceKm,
      cell.area,
    ]
      .map(escape)
      .join(",");
  });
  return [
    ...comments,
    "cell_id,issue_date,kind,variant,model,value,n_primary,municipality,province,department,nearest_place_name,nearest_place_distance_km,national_area_km2",
    ...rows,
    "",
  ].join("\r\n");
}
