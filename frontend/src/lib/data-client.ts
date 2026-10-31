import * as api from "./api";
import type { Cell, LiveIndex } from "./types";

export const staticMode = import.meta.env.MODE === "static";
type StaticClient = ReturnType<
  (typeof import("./static"))["createStaticClient"]
>;
let client: Promise<StaticClient> | undefined;
function staticClient(): Promise<StaticClient> {
  return (client ??= import("./static").then(({ createStaticClient }) =>
    createStaticClient(import.meta.env.BASE_URL),
  ));
}
export const liveClient = {
  async loadInitial(...args: Parameters<StaticClient["loadInitial"]>) {
    return (await staticClient()).loadInitial(...args);
  },
  async loadLayer(...args: Parameters<StaticClient["loadLayer"]>) {
    return (await staticClient()).loadLayer(...args);
  },
  async loadSeries(...args: Parameters<StaticClient["loadSeries"]>) {
    return (await staticClient()).loadSeries(...args);
  },
  async loadTrackRecord(...args: Parameters<StaticClient["loadTrackRecord"]>) {
    return (await staticClient()).loadTrackRecord(...args);
  },
};
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
