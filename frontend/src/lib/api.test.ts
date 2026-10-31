import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  getJson,
  exportUrl,
  loadWeeks,
  normalizeGrid,
  normalizeMeta,
  normalizeSeries,
  normalizeWeeks,
} from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("API client", () => {
  it("preserves null labels in successful JSON", async () => {
    vi.stubGlobal(
      "fetch",
      async () => new Response(JSON.stringify({ value: [null, 0, 1] })),
    );
    expect(await getJson("/layer")).toEqual({ value: [null, 0, 1] });
  });
  it("classifies network errors as offline", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });
    await expect(getJson("/meta")).rejects.toMatchObject({
      kind: "offline",
      status: 0,
    });
  });
  it("classifies missing estimates without masking validation errors", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(JSON.stringify({ detail: "No predictions available." }), {
          status: 404,
        }),
    );
    await expect(getJson("/layer?kind=estimate")).rejects.toMatchObject({
      kind: "missing",
      status: 404,
    });
    vi.stubGlobal("fetch", async () => new Response("{}", { status: 422 }));
    await expect(getJson("/layer")).rejects.toMatchObject({
      kind: "filters",
      status: 422,
    });
  });
  it("does not expose server exception text", async () => {
    vi.stubGlobal(
      "fetch",
      async () => new Response("private internal path", { status: 500 }),
    );
    await expect(getJson("/meta")).rejects.toThrow(
      "The data service could not complete this request.",
    );
  });
  it("handles invalid JSON and preserves explicit cancellation", async () => {
    vi.stubGlobal(
      "fetch",
      async () => new Response("<html>wrong endpoint</html>"),
    );
    await expect(getJson("/meta")).rejects.toBeInstanceOf(ApiError);
    vi.stubGlobal("fetch", async () => {
      throw new DOMException("Aborted", "AbortError");
    });
    await expect(getJson("/meta")).rejects.toMatchObject({
      name: "AbortError",
    });
  });
  it("encodes filters in a same-origin bounded CSV URL", () => {
    const url = exportUrl(
      "2024-09-02",
      "estimate",
      "logistic regression",
      "strict",
    );
    expect(url.startsWith("/api/v1/export?")).toBe(true);
    expect(new URL(url, "http://localhost").searchParams.get("model")).toBe(
      "logistic regression",
    );
    expect(new URL(url, "http://localhost").searchParams.get("format")).toBe(
      "csv",
    );
  });
  it("loads the last available week from a paginated catalog", async () => {
    const syntheticWeeks = Array.from({ length: 101 }, (_, index) => ({
      issue_date: new Date(Date.UTC(2020, 0, 6 + index * 7))
        .toISOString()
        .slice(0, 10),
      label_complete_share: 1,
      outage: false,
    }));
    vi.stubGlobal("fetch", async (url: string) => {
      const offset = Number(
        new URL(url, "http://localhost").searchParams.get("offset"),
      );
      return new Response(
        JSON.stringify({
          weeks: syntheticWeeks.slice(offset, offset + 100),
          total: syntheticWeeks.length,
          offset,
          limit: 100,
        }),
      );
    });
    const result = await loadWeeks();
    expect(result).toHaveLength(101);
    expect(result.at(-1)?.date).toBe(syntheticWeeks.at(-1)?.issue_date);
  });
});

describe("contract normalization with synthetic payloads", () => {
  it("reads metadata without inventing dates or model availability", () => {
    const meta = normalizeMeta({
      project_name: "Bolivia",
      protocol: { version: "1.1", sha256: "abc" },
      issue_date_range: { start: "2024-09-02", end: "2024-09-09" },
      available_layers: ["observed"],
      models: {},
      variants: [],
      dataset_snapshot_ids: { panel: "hash" },
      source_attributions: [
        {
          name: "geoBoundaries",
          url: "https://www.geoboundaries.org/",
          license: "CC BY 4.0",
        },
      ],
    });
    expect(meta.start).toBe("2024-09-02");
    expect(meta.models).toEqual({});
    expect(meta.protocolVersion).toBe("1.1");
    expect(meta.snapshots).toEqual({ panel: "hash" });
    expect(meta.layers).toEqual(["observed"]);
    expect(meta.attributions[0].license).toBe("CC BY 4.0");
  });
  it("reads columnar cells without changing their array index", () => {
    const grid = normalizeGrid({
      origin: [-180, -90],
      resolution_deg: 0.05,
      cell_id: ["r10c20", "r11c21"],
      row: [10, 11],
      col: [20, 21],
      department: ["A", "B"],
      national_area_km2: [10, 9],
    });
    expect(grid.cells[1]).toMatchObject({
      id: "r11c21",
      index: 1,
      row: 11,
      col: 21,
      area: 9,
    });
    expect(grid.bounds).toEqual({
      rowMin: 10,
      rowMax: 11,
      colMin: 20,
      colMax: 21,
    });
  });
  it("preserves available models separately for each variant", () => {
    const meta = normalizeMeta({
      models: {
        strict: ["strict/lightgbm"],
        retrospective: ["retrospective/logistic_regression"],
      },
      variants: ["strict", "retrospective"],
    });
    expect(meta.models).toEqual({
      strict: ["strict/lightgbm"],
      retrospective: ["retrospective/logistic_regression"],
    });
  });
  it("preserves missing observations and probability gaps in the time series", () => {
    expect(
      normalizeSeries({
        issue_date: ["2024-09-02"],
        y: [null],
        n_primary: [2],
        p: [null],
      }),
    ).toEqual([{ date: "2024-09-02", y: null, n: 2, p: null }]);
  });
  it("keeps observation completeness separate from short outages and low coverage", () => {
    expect(
      normalizeWeeks({
        weeks: [
          {
            issue_date: "2024-09-09",
            outage: true,
            target_complete: false,
            target_low_coverage: true,
            label_complete_share: 0,
          },
          {
            issue_date: "2024-09-02",
            outage: true,
            target_complete: true,
            target_low_coverage: true,
            label_complete_share: 1,
          },
        ],
      }),
    ).toEqual([
      {
        date: "2024-09-02",
        completeness: 1,
        targetComplete: true,
        outage: true,
        lowCoverage: true,
      },
      {
        date: "2024-09-09",
        completeness: 0,
        targetComplete: false,
        outage: true,
        lowCoverage: true,
      },
    ]);
  });
});
