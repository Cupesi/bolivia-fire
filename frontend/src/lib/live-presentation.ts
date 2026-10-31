export function publishedTime(value: string): string {
  return new Date(value).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function targetWindow(start: string, exclusiveEnd: string): string {
  const first = new Date(start + "T00:00:00Z");
  const last = new Date(Date.parse(exclusiveEnd + "T00:00:00Z") - 86400000);
  const format = (date: Date, year: boolean) =>
    new Intl.DateTimeFormat("en-GB", {
      day: "numeric",
      month: "short",
      ...(year ? { year: "numeric" } : {}),
      timeZone: "UTC",
    }).format(date);
  return `${format(first, first.getUTCFullYear() !== last.getUTCFullYear())} – ${format(last, true)} (UTC)`;
}
