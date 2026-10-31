import { screenPoint, type View } from "./projection";
import type { Grid, PopulatedPlace } from "./types";

export function boundaryLevels(zoom: number): number[] {
  return zoom >= 4 ? [0, 1, 2, 3] : zoom >= 2 ? [0, 1, 2] : [0, 1];
}

export interface PlaceLabel extends PopulatedPlace {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function placeLabels(
  grid: Grid,
  view: View,
  width: number,
  height: number,
  measure: (name: string) => number,
): PlaceLabel[] {
  const towns = new Map<
    number,
    PopulatedPlace & { col: number; row: number }
  >();
  for (const cell of grid.cells)
    for (const place of cell.populatedPlaces ?? []) {
      if (
        place.population >= 20000 &&
        Number.isFinite(place.geonames_id) &&
        !towns.has(place.geonames_id)
      )
        towns.set(place.geonames_id, {
          ...place,
          col: cell.col + 0.5,
          row: cell.row + 0.5,
        });
    }
  const labels: PlaceLabel[] = [];
  for (const town of [...towns.values()].sort(
    (a, b) => b.population - a.population || a.geonames_id - b.geonames_id,
  )) {
    const point = screenPoint(town.col, town.row, view);
    const label = {
      ...town,
      x: point.x + 5,
      y: point.y - 9,
      width: Math.ceil(measure(town.name)) + 8,
      height: 18,
    };
    if (
      label.x < 4 ||
      label.y < 4 ||
      label.x + label.width > width - 4 ||
      label.y + label.height > height - 38
    )
      continue;
    if (label.x + label.width > width - 158 && label.y < 48) continue;
    if (
      labels.some(
        (other) =>
          label.x < other.x + other.width + 4 &&
          label.x + label.width + 4 > other.x &&
          label.y < other.y + other.height + 4 &&
          label.y + label.height + 4 > other.y,
      )
    )
      continue;
    labels.push(label);
  }
  return labels;
}

export function nearestPlaceText(cell: Grid["cells"][number]): string {
  const place = cell.nearestPlace;
  if (!place) return "";
  const distance = place.distanceKm;
  return `Nearest place: ${place.name}${distance === null ? "" : `, ${distance > 0 && distance < 1 ? "<1" : Math.round(distance)} km`}`;
}
