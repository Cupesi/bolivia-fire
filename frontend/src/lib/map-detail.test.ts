import { describe, expect, it } from "vitest";
import { normalizeGrid } from "./api";
import { boundaryLevels, placeLabels, nearestPlaceText } from "./map-detail";
import { es } from "./messages/es";

describe("map detail with synthetic cells", () => {
  const grid = normalizeGrid({
    origin: [-180, -90],
    resolution_deg: 0.05,
    cell_id: ["r10c20", "r10c21", "r10c22"],
    row: [10, 10, 10],
    col: [20, 21, 22],
    department: ["Santa Cruz", "Santa Cruz", "Santa Cruz"],
    national_area_km2: [20, 20, 20],
    province: ["Ñuflo de Chávez", "Chiquitos", "Chiquitos"],
    municipality: ["Concepción", "San José", "San José"],
    nearest_place_name: ["Concepción", "San José", "San José"],
    nearest_place_distance_km: [12.4, 0, 2.1],
    populated_places: [
      [{ name: "Concepción", geonames_id: 1, population: 25000 }],
      [
        { name: "San José", geonames_id: 2, population: 50000 },
        { name: "Small", geonames_id: 3, population: 19999 },
      ],
      [{ name: "San José", geonames_id: 2, population: 50000 }],
    ],
  });

  it("preserves accents, zero distance and the exact cell alignment", () => {
    expect(grid.cells[0]).toMatchObject({
      municipality: "Concepción",
      province: "Ñuflo de Chávez",
      nearestPlace: { name: "Concepción", distanceKm: 12.4 },
    });
    expect(grid.cells[1].nearestPlace?.distanceKm).toBe(0);
    expect(() =>
      normalizeGrid({
        cell_id: ["a"],
        row: [1],
        col: [1],
        department: ["A"],
        national_area_km2: [1],
        origin: [0, 0],
        resolution_deg: 1,
        province: [],
      }),
    ).toThrow(/columns/);
  });

  it("keeps nearest-place names and distance units together in both languages", () => {
    expect(nearestPlaceText(grid.cells[0])).toBe(
      "Nearest place: Concepción, 12\u00a0km",
    );
    expect(
      nearestPlaceText(grid.cells[0], { language: "es", messages: es }),
    ).toBe("Lugar más cercano: Concepción, 12\u00a0km");
  });

  it("shows departments always and finer boundaries at the requested zoom thresholds", () => {
    expect(boundaryLevels(1)).toEqual([0, 1]);
    expect(boundaryLevels(2)).toEqual([0, 1, 2]);
    expect(boundaryLevels(3.99)).toEqual([0, 1, 2]);
    expect(boundaryLevels(4)).toEqual([0, 1, 2, 3]);
  });

  it("prioritizes larger towns, deduplicates IDs and rejects overlapping or clipped labels", () => {
    const labels = placeLabels(
      grid,
      { scale: 20, x: -340, y: 270 },
      220,
      160,
      () => 70,
    );
    expect(labels.map((label) => label.name)).toEqual(["San José"]);
    expect(labels[0]).toMatchObject({ geonames_id: 2 });
    expect(
      placeLabels(grid, { scale: 20, x: 0, y: 0 }, 220, 160, () => 70),
    ).toEqual([]);
  });
});
