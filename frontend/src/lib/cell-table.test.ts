import { describe, expect, it } from "vitest";
import { tableCells, nearestPlaceLabel, weekCsv } from "./cell-table";
import type { Cell, Meta } from "./types";

const syntheticCells: Cell[] = [
  {
    id: "a",
    index: 0,
    row: 0,
    col: 0,
    area: 20,
    department: "Santa Cruz",
    municipality: "Concepción",
    province: "Ñuflo de Chávez",
    nearestPlace: { name: 'San José, "Centro"', distanceKm: 12.4 },
  },
  {
    id: "b",
    index: 1,
    row: 0,
    col: 1,
    area: 21.25,
    department: "Beni",
    municipality: "Trinidad",
    province: "Cercado",
    nearestPlace: { name: "San José", distanceKm: 0.4 },
  },
  { id: "c", index: 2, row: 0, col: 2, area: 19, department: "Beni" },
];
const values = new Map([
  [0, 0.2],
  [1, 0.9],
  [2, null],
]);
const counts = new Map([
  [0, 3],
  [1, 12],
  [2, null],
]);

describe("cell table with synthetic cells", () => {
  it("finds accented places before applying Top N and sorting", () => {
    for (const query of [
      "concepcion",
      "Ñuflo de Chávez",
      "santa cruz",
      " centro ",
    ])
      expect(
        tableCells(
          syntheticCells,
          values,
          counts,
          query,
          "1",
          "value",
          true,
        ).map((cell) => cell.id),
      ).toEqual(["a"]);
    expect(
      tableCells(syntheticCells, values, counts, "unknown", "all", "id", false),
    ).toEqual([]);
  });
  it("sorts every column and leaves missing values last in both directions", () => {
    for (const [sort, ascending, descending] of [
      ["id", ["a", "b", "c"], ["c", "b", "a"]],
      ["municipality", ["a", "b", "c"], ["b", "a", "c"]],
      ["province", ["b", "a", "c"], ["a", "b", "c"]],
      ["department", ["b", "c", "a"], ["a", "b", "c"]],
      ["nearestPlace", ["b", "a", "c"], ["a", "b", "c"]],
      ["area", ["c", "a", "b"], ["b", "a", "c"]],
      ["value", ["a", "b", "c"], ["b", "a", "c"]],
      ["count", ["a", "b", "c"], ["b", "a", "c"]],
    ] as const) {
      expect(
        tableCells(syntheticCells, values, counts, "", "all", sort, false).map(
          (cell) => cell.id,
        ),
      ).toEqual(ascending);
      expect(
        tableCells(syntheticCells, values, counts, "", "all", sort, true).map(
          (cell) => cell.id,
        ),
      ).toEqual(descending);
    }
  });
  it("formats nearest distances like the map and retains missing distances", () => {
    expect(nearestPlaceLabel(syntheticCells[0])).toBe(
      'San José, "Centro" · 12 km',
    );
    expect(nearestPlaceLabel(syntheticCells[1])).toBe("San José · <1 km");
    expect(nearestPlaceLabel(syntheticCells[2])).toBe("Unassigned");
    expect(
      nearestPlaceLabel({
        ...syntheticCells[0],
        nearestPlace: { name: "San José", distanceKm: null },
      }),
    ).toBe("San José");
  });
  it("exports the complete loaded population, escaped place names and provenance", () => {
    const meta = {
      protocolHash: "synthetic-protocol",
      snapshots: { cells: "synthetic-cells" },
      attributions: [],
    } as unknown as Meta;
    const csv = weekCsv(
      syntheticCells,
      {
        cell_index: [0, 2],
        value: [0.234567, null],
        n_primary: [3, null],
        variant: "strict",
        model: "synthetic/model",
      },
      "2026-10-26",
      "estimate",
      meta,
    );
    expect(csv).toContain(
      "# project=Bolivia Fire; protocol_sha256=synthetic-protocol",
    );
    expect(csv).toContain('"cells":"synthetic-cells"');
    const records = csv
      .split("\r\n")
      .filter((line) => line && !line.startsWith("#"));
    expect(records).toEqual([
      "cell_id,issue_date,kind,variant,model,value,n_primary,municipality,province,department,nearest_place_name,nearest_place_distance_km,national_area_km2",
      'a,2026-10-26,estimate,strict,synthetic/model,0.234567,3,Concepción,Ñuflo de Chávez,Santa Cruz,"San José, ""Centro""",12.4,20',
      "c,2026-10-26,estimate,strict,synthetic/model,,,,,Beni,,,19",
    ]);
  });
});
