import { expect, it } from "vitest";
import { readFile } from "node:fs/promises";
import { verificationGroups } from "./live-presentation";
import { parseTrackRecord } from "./static";

it("keeps every synthetic scored week in one ordered archive group", async () => {
  const csv = await readFile(
    new URL(
      "../../e2e/fixtures/live-data/verification/track_record.csv",
      import.meta.url,
    ),
    "utf8",
  );
  const records = parseTrackRecord(csv).reverse();
  const before = structuredClone(records);
  const groups = verificationGroups(records, true);
  expect(groups).toHaveLength(1);
  expect(groups[0].kind).toBe("archive");
  expect(groups[0].rows.map((row) => row.issue_date)).toEqual([
    "2026-10-12",
    "2026-10-19",
  ]);
  expect(groups[0].rows.some((row) => row.prospective)).toBe(true);
  expect(groups[0].rows.some((row) => !row.prospective)).toBe(true);
  expect(records).toEqual(before);
});

it("retains the prospective and backfill groups for a non-retired catalog", async () => {
  const csv = await readFile(
    new URL(
      "../../e2e/fixtures/live-data/verification/track_record.csv",
      import.meta.url,
    ),
    "utf8",
  );
  const groups = verificationGroups(parseTrackRecord(csv), false);
  expect(groups.map((group) => group.kind)).toEqual([
    "prospective",
    "backfill",
  ]);
  expect(groups[0].rows.map((row) => row.issue_date)).toEqual(["2026-10-19"]);
  expect(groups[1].rows.map((row) => row.issue_date)).toEqual(["2026-10-12"]);
  expect(verificationGroups([], true)).toEqual([{ kind: "archive", rows: [] }]);
});
