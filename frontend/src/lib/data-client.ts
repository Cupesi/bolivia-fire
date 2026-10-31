import * as api from "./api";
import { createStaticClient } from "./static";
import type { Cell, LiveIndex } from "./types";

export const staticMode = import.meta.env.MODE === "static";
export const liveClient = createStaticClient(import.meta.env.BASE_URL);
export type InitialData = Awaited<ReturnType<typeof api.loadInitial>> & {
  live?: LiveIndex;
};
export async function loadInitial(signal?: AbortSignal): Promise<InitialData> {
  return staticMode ? liveClient.loadInitial(signal) : api.loadInitial(signal);
}
export const loadLayer = staticMode ? liveClient.loadLayer : api.loadLayer;
export async function loadSeries(
  cell: Cell,
  start: string,
  end: string,
  model: string,
  variant: string,
  signal: AbortSignal,
) {
  if (staticMode) return liveClient.loadSeries(cell.index, start, end, signal);
  const query = new URLSearchParams({ start, end });
  if (model) query.set("model", model);
  if (variant) query.set("variant", variant);
  return api.normalizeSeries(
    await api.getJson(
      `/cells/${encodeURIComponent(cell.id)}/series?${query}`,
      signal,
    ),
  );
}
