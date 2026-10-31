import type { LayerKind, NullableNumber } from "./types";
import { englishState, formatNumber, type LanguageState } from "./i18n";

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
  state: LanguageState = englishState,
): string {
  if (value == null || !Number.isFinite(value)) return state.messages.missing;
  return kind === "estimate"
    ? formatNumber(value, state.language, 3)
    : value > 0
      ? state.messages.detected
      : state.messages.notDetected;
}
