import { flushSync } from "svelte";
import { writable } from "svelte/store";
import { en, type MessageKey, type Messages } from "./messages/en";

export type Language = "en" | "es";
export interface LanguageState {
  language: Language;
  messages: Messages;
}
export const englishState: LanguageState = { language: "en", messages: en };
export const i18n = writable<LanguageState>(englishState);
export const languageStorageKey = "bolivia-fire-language";
let spanish: Promise<Messages> | undefined;
let selection = 0;

export function resolveLanguage(
  query: string,
  stored: string | null,
  languages: readonly string[],
): Language {
  const requested = new URLSearchParams(query).get("lang");
  if (requested === "en" || requested === "es") return requested;
  if (stored === "en" || stored === "es") return stored;
  return languages.some((language) => /^es(?:-|$)/i.test(language))
    ? "es"
    : "en";
}

export function initialLanguage(): Language {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(languageStorageKey);
  } catch {
    /* Storage can be disabled. */
  }
  return resolveLanguage(
    location.search,
    stored,
    navigator.languages ?? [navigator.language],
  );
}

export async function selectLanguage(
  language: Language,
  persist = true,
): Promise<void> {
  const request = ++selection;
  const loadStart = performance.now();
  const messages =
    language === "es"
      ? await (spanish ??= import("./messages/es")
          .then((module) => module.es)
          .catch((error: unknown) => {
            spanish = undefined;
            throw error;
          }))
      : en;
  if (request !== selection) return;
  performance.measure("language-load", {
    start: loadStart,
    end: performance.now(),
  });
  const start = performance.now();
  flushSync(() => {
    i18n.set({ language, messages });
  });
  document.documentElement.lang = language;
  document.title = messages.pageTitle;
  if (persist) {
    try {
      localStorage.setItem(languageStorageKey, language);
    } catch {
      /* The current selection still works without storage. */
    }
    const url = new URL(location.href);
    if (url.searchParams.has("lang")) {
      url.searchParams.set("lang", language);
      history.replaceState(null, "", url);
    }
  }
  performance.measure("language-swap", { start, end: performance.now() });
}

export function message(
  state: LanguageState,
  key: MessageKey,
  values: Record<string, string | number>,
): string {
  return state.messages[key].replace(
    /\{(\w+)\}/g,
    (placeholder, name: string) => String(values[name] ?? placeholder),
  );
}

const locale = (language: Language) => (language === "es" ? "es-BO" : "en");
type CountMessageKey = "tablePage" | "layerSummary" | "inputDaysInThe365Day";

export function countMessage(
  state: LanguageState,
  key: CountMessageKey,
  count: number,
  values: Record<string, string | number> = {},
): string {
  const rules = new Intl.PluralRules(locale(state.language));
  return message(state, rules.select(count) === "one" ? `${key}One` : key, {
    ...values,
    count: formatNumber(count, state.language),
  });
}

const numberFormats: Record<string, Intl.NumberFormat> = {};
export function formatNumber(
  value: number,
  language: Language,
  digits = 0,
): string {
  const key = `${language}:${digits}`;
  const format = (numberFormats[key] ??= new Intl.NumberFormat(
    locale(language),
    {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    },
  ));
  return format.format(value);
}

export function formatUnit(
  value: number,
  unit: string,
  language: Language,
  digits = 0,
): string {
  return `${formatNumber(value, language, digits)}\u00a0${unit}`;
}

export function formatDistance(value: number, language: Language): string {
  return value > 0 && value < 1
    ? "<1\u00a0km"
    : formatUnit(value, "km", language);
}

export function formatPercent(
  value: number,
  language: Language,
  digits = 1,
): string {
  return `${formatNumber(value * 100, language, digits)}${language === "es" ? "\u00a0" : ""}%`;
}

const dateFormats: Record<string, Intl.DateTimeFormat> = {};
export function formatDate(
  value: string,
  language: Language,
  year = true,
): string {
  const key = `${language}:${year}`;
  const format = (dateFormats[key] ??= new Intl.DateTimeFormat(
    locale(language),
    {
      day: "numeric",
      month: "short",
      ...(year ? { year: "numeric" } : {}),
      timeZone: "UTC",
    },
  ));
  const parts = format.formatToParts(
    new Date(value.length === 10 ? value + "T00:00:00Z" : value),
  );
  const part = (name: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === name)?.value ?? "";
  return `${part("day")} ${part("month")}${year ? ` ${part("year")}` : ""}`;
}

export function formatTime(value: string, language: Language): string {
  const time = new Intl.DateTimeFormat(locale(language), {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "UTC",
  }).format(new Date(value));
  return `${formatDate(value, language)}, ${time} UTC`;
}

export function formatWindow(
  start: string,
  exclusiveEnd: string,
  language: Language,
): string {
  const last = new Date(Date.parse(exclusiveEnd + "T00:00:00Z") - 86400000)
    .toISOString()
    .slice(0, 10);
  return message(englishState, "dateRange", {
    start: formatDate(start, language, start.slice(0, 4) !== last.slice(0, 4)),
    end: formatDate(last, language),
  });
}

const englishKeys = new Map(
  Object.entries(en).map(([key, value]) => [
    value.replaceAll("\u00a0", " "),
    key as MessageKey,
  ]),
);
export function localizeText(state: LanguageState, value: string): string {
  const key = englishKeys.get(value.replaceAll("\u00a0", " "));
  if (key) return state.messages[key];
  if (state.language === "en") return value;
  const parts = value.includes("; ")
    ? value.split(/(; )/)
    : value.split(/(: )/);
  return parts.length > 1
    ? parts
        .map((part, index) => (index % 2 ? part : localizeText(state, part)))
        .join("")
    : value;
}

export function errorText(state: LanguageState, error: Error): string {
  const match =
    /^(Data file unavailable|Data file is unreadable): (.+)\.$/.exec(
      error.message,
    );
  if (match)
    return message(
      state,
      match[1] === "Data file unavailable"
        ? "dataFileUnavailablePath"
        : "dataFileIsUnreadablePath",
      { path: match[2] },
    );
  return englishKeys.has(error.message)
    ? localizeText(state, error.message)
    : state.messages.theRequestCouldNotBeCompleted;
}
