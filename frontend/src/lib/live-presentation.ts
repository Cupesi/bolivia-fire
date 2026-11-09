import { formatTime, formatWindow, type Language } from "./i18n";
import type { TrackRecord } from "./types";

export function verificationGroups(
  records: readonly TrackRecord[],
  ended: boolean,
): {
  kind: "archive" | "prospective" | "backfill";
  rows: TrackRecord[];
}[] {
  if (ended)
    return [
      {
        kind: "archive",
        rows: [...records].sort((a, b) =>
          a.issue_date.localeCompare(b.issue_date),
        ),
      },
    ];
  return [
    {
      kind: "prospective",
      rows: records.filter((record) => record.prospective),
    },
    { kind: "backfill", rows: records.filter((record) => !record.prospective) },
  ];
}

export function publishedTime(
  value: string,
  language: Language = "en",
): string {
  return formatTime(value, language);
}

export function targetWindow(
  start: string,
  exclusiveEnd: string,
  language: Language = "en",
): string {
  return formatWindow(start, exclusiveEnd, language);
}
