import type { Meta } from "./types";

const labels: Record<string, string> = {
  protocol_sha256: "Protocol SHA-256",
  experiments_sha256: "Experiments SHA-256",
  sources_sha256: "Sources SHA-256",
  cells_sha256: "Cells SHA-256",
  cell_places_sha256: "Cell places SHA-256",
  evaluation_sources_sha256: "Evaluation sources SHA-256",
  firms_manifest_sha256: "FIRMS manifest SHA-256",
  outage_snapshot_sha256: "Outage snapshot SHA-256",
  detections_sha256: "Detections SHA-256",
  acquisition_days_sha256: "Acquisition days SHA-256",
  frozen_sp_bundle_sha256: "Frozen SP bundle SHA-256",
  outage_sha256: "Outage snapshot SHA-256",
};

function panelIdentifiers(snapshots: Record<string, unknown>): string | null {
  const panel = snapshots.panel;
  if (!panel || typeof panel !== "object" || Array.isArray(panel)) return null;
  const years = Object.entries(panel).flatMap(([year, identifiers]) => {
    if (typeof identifiers === "string" && identifiers)
      return [[year, identifiers]];
    if (
      !identifiers ||
      typeof identifiers !== "object" ||
      Array.isArray(identifiers)
    )
      return [];
    const entries = Object.entries(identifiers).flatMap(([key, value]) =>
      labels[key] &&
      typeof value === "string" &&
      value &&
      value !== snapshots[key]
        ? [[labels[key], value]]
        : [],
    );
    return entries.length ? [[year, Object.fromEntries(entries)]] : [];
  });
  return years.length
    ? JSON.stringify(Object.fromEntries(years), null, 2)
    : null;
}

export function provenanceRows(meta: Meta): [string, string][] {
  const rows: [string, unknown][] = [
    ["Protocol version", meta.protocolVersion],
    ["Protocol SHA-256", meta.protocolHash || meta.snapshots.protocol_sha256],
    ["Cells SHA-256", meta.snapshots.cells_sha256],
    ["Panel snapshot", panelIdentifiers(meta.snapshots)],
    ["Sources SHA-256", meta.snapshots.sources_sha256],
    ...Object.entries(labels)
      .filter(
        ([key]) =>
          !["protocol_sha256", "cells_sha256", "sources_sha256"].includes(key),
      )
      .map(([key, label]): [string, unknown] => [label, meta.snapshots[key]]),
    ["Model identifier", meta.modelId],
    ["Model SHA-256", meta.modelHash],
    ["Calibrator SHA-256", meta.calibratorHash],
    ["Final-test opening (UTC)", meta.finalTestOpened],
  ];
  return rows.filter(
    (row): row is [string, string] =>
      typeof row[1] === "string" && Boolean(row[1].trim()),
  );
}
