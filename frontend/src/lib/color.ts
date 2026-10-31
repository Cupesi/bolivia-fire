import type { LayerKind, NullableNumber } from "./types";

export const MISSING_COLOR = "#aeb5b6";
const stops = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
];

export function colorForValue(
  value: NullableNumber | undefined,
  kind: LayerKind,
): string {
  if (value == null || !Number.isFinite(value)) return MISSING_COLOR;
  if (kind === "observed") return value > 0 ? "#287d72" : "#f0efdc";
  const position = Math.max(0, Math.min(1, value)) * (stops.length - 1);
  const lower = Math.min(Math.floor(position), stops.length - 2);
  const fraction = position - lower;
  return (
    "#" +
    stops[lower]
      .map((channel, index) =>
        Math.round(channel + fraction * (stops[lower + 1][index] - channel))
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}

export function formatValue(
  value: NullableNumber | undefined,
  kind: LayerKind,
): string {
  if (value == null || !Number.isFinite(value)) return "Missing";
  return kind === "estimate"
    ? value.toFixed(3)
    : value > 0
      ? "Detected"
      : "Not detected";
}
