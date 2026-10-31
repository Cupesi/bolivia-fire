import { formatTime, formatWindow, type Language } from "./i18n";

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
