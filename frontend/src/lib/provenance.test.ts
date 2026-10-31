import { describe, expect, it } from "vitest";
import { normalizeMeta } from "./api";
import { provenanceRows } from "./provenance";

describe("provenance with synthetic metadata", () => {
  it("omits absent identifiers instead of inventing placeholder values", () => {
    expect(provenanceRows(normalizeMeta({}))).toEqual([]);
    expect(
      provenanceRows(normalizeMeta({ protocol: { version: "1.1" } })),
    ).toEqual([["Protocol version", "1.1"]]);
  });

  it("retains supplied shared hashes and distinct annual panel identifiers", () => {
    const rows = provenanceRows(
      normalizeMeta({
        protocol: { version: "1.1", sha256: "synthetic-protocol" },
        dataset_snapshot_ids: {
          cells_sha256: "synthetic-cells",
          sources_sha256: "synthetic-sources",
          experiments_sha256: "synthetic-experiments",
          panel: {
            "2025": {
              cells_sha256: "synthetic-cells",
              detections_sha256: "synthetic-detections",
            },
          },
        },
      }),
    );
    expect(rows).toContainEqual([
      "Experiments SHA-256",
      "synthetic-experiments",
    ]);
    expect(rows).toContainEqual(["Cells SHA-256", "synthetic-cells"]);
    expect(rows.find(([name]) => name === "Panel snapshot")?.[1]).toBe(
      '{\n  "2025": {\n    "Detections SHA-256": "synthetic-detections"\n  }\n}',
    );
  });
  it("renders frozen artifact hashes, panel hashes and the recorded final-test time", () => {
    const rows = provenanceRows(
      normalizeMeta({
        model_id: "synthetic-model",
        source_model_sha256: "synthetic-model-hash",
        source_calibrator_sha256: "synthetic-calibrator",
        primary_opening_utc: "2026-10-01T00:00:00Z",
        dataset_snapshot_ids: { panel: { "2025": "synthetic-panel" } },
      }),
    );
    expect(rows).toContainEqual(["Model identifier", "synthetic-model"]);
    expect(rows).toContainEqual(["Model SHA-256", "synthetic-model-hash"]);
    expect(rows).toContainEqual(["Calibrator SHA-256", "synthetic-calibrator"]);
    expect(rows).toContainEqual([
      "Final-test opening (UTC)",
      "2026-10-01T00:00:00Z",
    ]);
    expect(rows.find(([name]) => name === "Panel snapshot")?.[1]).toContain(
      '"2025": "synthetic-panel"',
    );
  });
});
