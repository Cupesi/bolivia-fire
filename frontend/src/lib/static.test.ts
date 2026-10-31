import { readFile } from "node:fs/promises";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createStaticClient, parseTrackRecord } from "./static";

const fixture = new URL("../../e2e/fixtures/live-data/", import.meta.url);
const client = () => createStaticClient("/bolivia-fire/");
const signal = () => new AbortController().signal;

function syntheticFetch() {
  vi.stubGlobal("fetch", async (url: string) => {
    if (!url.startsWith("/bolivia-fire/data/"))
      return new Response("", { status: 404 });
    const path = url.slice("/bolivia-fire/data/".length);
    try {
      return new Response(await readFile(new URL(path, fixture), "utf8"));
    } catch {
      return new Response("", { status: 404 });
    }
  });
}
afterEach(() => vi.unstubAllGlobals());

describe("static client with synthetic data", () => {
  it("loads the catalog and aligned grid under the Pages base path", async () => {
    syntheticFetch();
    const result = await client().loadInitial(signal());
    expect(result.live.latest_issue).toBe("2026-10-26");
    expect(result.weeks.map((week) => week.date)).toEqual([
      "2026-10-12",
      "2026-10-19",
      "2026-10-26",
    ]);
    expect(result.grid.cells[1]).toMatchObject({
      id: "r1460c2321",
      index: 1,
      area: 21,
    });
    expect(result.meta.layers).toContain("estimate");
  });
  it("reads source credits and protocol provenance from the live pipeline metadata", async () => {
    syntheticFetch();
    const original = fetch;
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.endsWith("static/meta.json"))
        return new Response(
          JSON.stringify({
            label: "Synthetic experimental estimate",
            model_id: "synthetic/lightgbm",
            protocol_sha256: "synthetic-live-protocol",
            frozen_sp_bundle_sha256: "synthetic-sp-bundle",
            outage_sha256: "synthetic-outages",
            attribution: [
              {
                name: "NASA FIRMS",
                url: "https://firms.modaps.eosdis.nasa.gov/",
                license: "synthetic licence",
              },
            ],
          }),
        );
      return original(url);
    });
    const result = await client().loadInitial(signal());
    expect(result.meta.protocolHash).toBe("synthetic-live-protocol");
    expect(result.meta.attributions[0]).toMatchObject({
      name: "NASA FIRMS",
      license: "synthetic licence",
    });
    expect(result.meta.snapshots).toMatchObject({
      frozen_sp_bundle_sha256: "synthetic-sp-bundle",
      outage_sha256: "synthetic-outages",
    });
  });
  it("maps forecast probabilities and generation metadata without filling gaps", async () => {
    syntheticFetch();
    const layer = await client().loadLayer(
      "2026-10-26",
      "estimate",
      "",
      "",
      signal(),
    );
    expect(layer.value).toEqual([0.9, 0.7, 0.2, null]);
    expect(layer.model).toBe("synthetic/lightgbm");
    expect(layer.forecast?.target_end).toBe("2026-11-02");
  });
  it("preserves incomplete observation labels while retaining counts", async () => {
    syntheticFetch();
    const layer = await client().loadLayer(
      "2026-10-26",
      "observed",
      "",
      "",
      signal(),
    );
    expect(layer.value).toEqual([null, null, null, null]);
    expect(layer.n_primary).toEqual([1, 0, 2, 0]);
    expect(layer.observation?.complete).toBe(false);
  });
  it("joins recent history by cell index and preserves missing observations", async () => {
    syntheticFetch();
    const series = await client().loadSeries(
      1,
      "2026-10-19",
      "2026-10-26",
      signal(),
    );
    expect(series).toEqual([
      { date: "2026-10-19", y: 1, n: 3, p: 0.8 },
      { date: "2026-10-26", y: null, n: 0, p: 0.7 },
    ]);
  });
  it("rejects malformed layers instead of aligning the wrong cells", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(
          JSON.stringify({
            issue_date: "2026-10-26",
            cell_index: [0, 1],
            p: [0.4],
          }),
        ),
    );
    await expect(
      client().loadLayer("2026-10-26", "estimate", "", "", signal()),
    ).rejects.toMatchObject({ kind: "invalid" });
  });
  it("distinguishes missing files, HTML fallback responses, offline and cancellation", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 404 }));
    await expect(client().loadInitial(signal())).rejects.toMatchObject({
      kind: "missing",
    });
    vi.stubGlobal("fetch", async () => new Response("<!doctype html>"));
    await expect(client().loadInitial(signal())).rejects.toMatchObject({
      kind: "invalid",
    });
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("offline");
    });
    await expect(client().loadInitial(signal())).rejects.toMatchObject({
      kind: "offline",
    });
    vi.stubGlobal("fetch", async () => {
      throw new DOMException("Aborted", "AbortError");
    });
    await expect(client().loadInitial(signal())).rejects.toMatchObject({
      name: "AbortError",
    });
  });
  it("parses metric nulls and quoted CSV notes without inventing zero scores", () => {
    const records = parseTrackRecord(
      'issue_date,n_cells,positives,ap,brier,roc_auc,recall_area_5,recall_area_10,ece,climatology_ap,notes,prospective,generated_utc,publication_lag_hours\r\n2026-10-19,4,0,0,0.1,,0,0,0.1,0,"synthetic, no positives",False,2026-10-31T06:00:00Z,294\r\n',
    );
    expect(records[0]).toMatchObject({
      issue_date: "2026-10-19",
      ap: 0,
      roc_auc: null,
      notes: "synthetic, no positives",
      prospective: false,
      generated_utc: "2026-10-31T06:00:00Z",
    });
    expect(
      parseTrackRecord(
        "issue_date,n_cells,positives,ap,brier,roc_auc,recall_area_5,recall_area_10,ece,climatology_ap,notes,prospective,generated_utc,publication_lag_hours\n",
      ),
    ).toEqual([]);
  });
  it("requires explicit timing provenance instead of treating backfill as prospective", () => {
    expect(() =>
      parseTrackRecord(
        "issue_date,n_cells,positives,ap,brier,roc_auc,recall_area_5,recall_area_10,ece,climatology_ap,notes\n2026-10-19,4,2,1,0.1,1,0.5,0.5,0.1,0.5,synthetic\n",
      ),
    ).toThrow(/required columns/);
  });
  it("rejects non-null labels in incomplete observations", async () => {
    const raw = JSON.parse(
      await readFile(new URL("observations/2026-10-26.json", fixture), "utf8"),
    );
    raw.y[0] = 0;
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(raw)));
    await expect(
      client().loadLayer("2026-10-26", "observed", "", "", signal()),
    ).rejects.toMatchObject({ kind: "invalid" });
  });
});
