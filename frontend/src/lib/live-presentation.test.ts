import { expect, it } from "vitest";
import { publishedTime, targetWindow } from "./live-presentation";

it("shows publication minutes in UTC without fractional seconds", () => {
  expect(publishedTime("2026-10-31T18:03:15.451163+00:00")).toBe(
    "31 Oct 2026, 18:03 UTC",
  );
  expect(publishedTime("2026-10-31T14:03:15-04:00")).toBe(
    "31 Oct 2026, 18:03 UTC",
  );
});

it("shows inclusive target dates while preserving the exclusive end in source data", () => {
  expect(targetWindow("2026-10-26", "2026-11-02")).toBe(
    "26 Oct – 1 Nov 2026 (UTC)",
  );
  expect(targetWindow("2026-12-28", "2027-01-04")).toBe(
    "28 Dec 2026 – 3 Jan 2027 (UTC)",
  );
  expect(targetWindow("2026-10-26", "2026-11-02", "es")).toBe(
    "26 oct – 1 nov 2026 (UTC)",
  );
});
