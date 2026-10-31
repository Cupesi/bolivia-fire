import type { Meta } from "./types";
import { en, type Messages } from "./messages/en";

const labels = (messages: Messages): Record<string, string> => ({
  protocol_sha256: messages.protocolSha256,
  experiments_sha256: messages.experimentsSha256,
  sources_sha256: messages.sourcesSha256,
  cells_sha256: messages.cellsSha256,
  cell_places_sha256: messages.cellPlacesSha256,
  evaluation_sources_sha256: messages.evaluationSourcesSha256,
  firms_manifest_sha256: messages.firmsManifestSha256,
  outage_snapshot_sha256: messages.outageSnapshotSha256,
  detections_sha256: messages.detectionsSha256,
  acquisition_days_sha256: messages.acquisitionDaysSha256,
  frozen_sp_bundle_sha256: messages.frozenSpBundleSha256,
  outage_sha256: messages.outageSnapshotSha256,
});

function panelIdentifiers(
  snapshots: Record<string, unknown>,
  labels: Record<string, string>,
): string | null {
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

export function provenanceRows(
  meta: Meta,
  messages: Messages = en,
): [string, string][] {
  const names = labels(messages);
  const rows: [string, unknown][] = [
    [messages.protocolVersion, meta.protocolVersion],
    [
      messages.protocolSha256,
      meta.protocolHash || meta.snapshots.protocol_sha256,
    ],
    [messages.cellsSha256, meta.snapshots.cells_sha256],
    [messages.panelSnapshot, panelIdentifiers(meta.snapshots, names)],
    [messages.sourcesSha256, meta.snapshots.sources_sha256],
    ...Object.entries(names)
      .filter(
        ([key]) =>
          !["protocol_sha256", "cells_sha256", "sources_sha256"].includes(key),
      )
      .map(([key, label]): [string, unknown] => [label, meta.snapshots[key]]),
    [messages.modelIdentifier, meta.modelId],
    [messages.modelSha256, meta.modelHash],
    [messages.calibratorSha256, meta.calibratorHash],
    [messages.finalTestOpeningUtc, meta.finalTestOpened],
  ];
  return rows.filter(
    (row): row is [string, string] =>
      typeof row[1] === "string" && Boolean(row[1].trim()),
  );
}
