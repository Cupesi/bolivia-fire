import { describe, expect, it } from "vitest";
import { en } from "./messages/en";
import { es } from "./messages/es";
import {
  formatDate,
  formatNumber,
  formatTime,
  formatWindow,
  resolveLanguage,
  errorText,
  localizeText,
  countMessage,
  formatPercent,
  formatUnit,
  formatDistance,
} from "./i18n";
import { formatValue } from "./color";

describe("message catalogs", () => {
  it("has identical nonempty keys and valid UTF-8 text", () => {
    expect(Object.keys(es).sort()).toEqual(Object.keys(en).sort());
    for (const catalog of [en, es]) {
      expect(
        Object.values(catalog).every((value) => value.trim().length > 0),
      ).toBe(true);
      expect(JSON.stringify(catalog)).not.toMatch(/�|Ã|Â|â€/);
    }
    expect(JSON.stringify(es)).toMatch(/detecciones satelitales/);
    expect(JSON.stringify(es)).toMatch(/ñ/);
    expect(JSON.stringify(es)).toMatch(/¿/);
    expect(JSON.stringify(es)).toMatch(/–/);
    expect(
      new TextDecoder("utf-8", { fatal: true }).decode(
        new TextEncoder().encode("áéíóúñ¿¡–"),
      ),
    ).toBe("áéíóúñ¿¡–");
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(es[key].match(/\{\w+\}/g) ?? []).toEqual(
        en[key].match(/\{\w+\}/g) ?? [],
      );
    }
  });
});

describe("language resolution", () => {
  it("prioritizes a valid query, then storage, then navigator languages", () => {
    expect(resolveLanguage("?lang=es", "en", ["en"])).toBe("es");
    expect(resolveLanguage("?lang=en", "es", ["es-BO"])).toBe("en");
    expect(resolveLanguage("?lang=fr", "en", ["es-BO"])).toBe("en");
    expect(resolveLanguage("", "es", ["en"])).toBe("es");
    expect(resolveLanguage("", null, ["en-US", "es-BO"])).toBe("es");
    expect(resolveLanguage("", "invalid", ["es-AR"])).toBe("es");
    expect(resolveLanguage("", null, ["fr"])).toBe("en");
  });
});

describe("localized presentation", () => {
  it("translates combined verification notes and undefined metric reasons", () => {
    const state = { language: "es" as const, messages: es };
    expect(
      localizeText(
        state,
        "average_precision: single_class; climatology_ap: unavailable or single class; climatology uses department fallback for cells without training cell climatology",
      ),
    ).toBe(
      "AP: una sola clase; AP de climatología: no disponible o una sola clase; Las celdas sin climatología propia calculada con datos de entrenamiento usan la climatología departamental de entrenamiento",
    );
    expect(
      localizeText(state, "recall_at_area_budget_5pct: no_positives"),
    ).toBe("Sensibilidad al 5\u00a0% del área: sin positivos");
  });
  it("recognizes source prose when presentation uses nonbreaking unit spaces", () => {
    expect(
      localizeText(
        { language: "es", messages: es },
        en.nrtHasNoTypeFieldNominal.replaceAll("\u00a0", " "),
      ),
    ).toBe(es.nrtHasNoTypeFieldNominal);
  });
  it("keeps identities intact and translates missing values and stored errors", () => {
    const state = { language: "es" as const, messages: es };
    expect(formatValue(0.602, "estimate", state)).toBe("0,602");
    expect(formatValue(null, "observed", state)).toBe("Faltante");
    expect(formatValue(1, "observed", state)).toBe("Con detecciones");
    expect(errorText(state, new Error(en.noPreparedDataAreAvailableFor))).toBe(
      "No hay datos preparados para esta selección.",
    );
    expect(
      errorText(
        state,
        new Error("Data file unavailable: forecasts/2026-10-26.json."),
      ),
    ).toBe("Archivo de datos no disponible: forecasts/2026-10-26.json.");
    for (const name of [
      "Concepción",
      "Ñuflo de Chávez",
      "NASA FIRMS",
      "VIIRS",
      "GeoNames",
      "geoBoundaries",
      "CHIRPS",
    ])
      expect(localizeText(state, name)).toBe(name);
  });
  it("formats probabilities, areas, thousands and distances with the active locale", () => {
    expect(formatNumber(0.602, "en", 3)).toBe("0.602");
    expect(formatNumber(0.602, "es", 3)).toBe("0,602");
    expect(formatNumber(29.57, "es", 2)).toBe("29,57");
    expect(formatNumber(37146, "en")).toBe("37,146");
    expect(formatNumber(37146, "es")).toBe("37.146");
    expect(formatNumber(12.4, "es", 1)).toBe("12,4");
  });
  it.each([
    [0, "0 cells", "0 celdas", "0 input days", "0 días no observados"],
    [1, "1 cell", "1 celda", "1 input day", "1 día no observado"],
    [2, "2 cells", "2 celdas", "2 input days", "2 días no observados"],
  ])(
    "selects localized count forms for %i",
    (count, enCells, esCells, enDays, esDays) => {
      for (const [state, cells, days] of [
        [{ language: "en" as const, messages: en }, enCells, enDays],
        [{ language: "es" as const, messages: es }, esCells, esDays],
      ] as const) {
        expect(
          countMessage(state, "tablePage", count, { page: 1, pages: 1 }),
        ).toBe(
          `${state.language === "es" ? "Página 1 de 1" : "Page 1 of 1"} · ${cells}`,
        );
        expect(
          countMessage(state, "layerSummary", count, {
            complete: 0,
            missing: count,
          }),
        ).toBe(
          `${state.language === "en" ? "Showing " : ""}${cells} · 0 ${state.language === "es" ? "con datos" : "with data"} · ${count} ${state.language === "es" ? "sin datos" : "missing"}`,
        );
        expect(countMessage(state, "inputDaysInThe365Day", count)).toBe(
          state.language === "es"
            ? `${days} en el período de entrada de 365 días (cortes del sensor)`
            : `${days} in the 365-day window ${count === 1 ? "was" : "were"} unobserved (sensor outages)`,
        );
      }
    },
  );
  it("formats percentages per locale and keeps measurement units with their numbers", () => {
    expect(formatPercent(1, "es", 1)).toBe("100,0\u00a0%");
    expect(formatPercent(1, "en", 1)).toBe("100.0%");
    expect(formatPercent(0.05, "es", 0)).toBe("5\u00a0%");
    expect(formatPercent(0.05, "en", 0)).toBe("5%");
    expect(formatUnit(29.57, "km²", "es", 2)).toBe("29,57\u00a0km²");
    expect(formatUnit(29.57, "km²", "en", 2)).toBe("29.57\u00a0km²");
    expect(formatUnit(12, "km", "es")).toBe("12\u00a0km");
    expect(formatUnit(6, "h", "en")).toBe("6\u00a0h");
    for (const language of ["en", "es"] as const) {
      expect(formatDistance(0, language)).toBe("0\u00a0km");
      expect(formatDistance(0.4, language)).toBe("<1\u00a0km");
      expect(formatDistance(12, language)).toBe("12\u00a0km");
    }
  });
  it("uses UTC for dates, inclusive target windows and publication times", () => {
    expect(formatWindow("2026-10-26", "2026-11-02", "es")).toBe(
      "26 oct – 1 nov 2026 (UTC)",
    );
    expect(formatWindow("2026-12-28", "2027-01-04", "en")).toBe(
      "28 Dec 2026 – 3 Jan 2027 (UTC)",
    );
    expect(formatDate("2026-10-26", "es")).toBe("26 oct 2026");
    expect(formatTime("2026-10-31T14:03:15-04:00", "es")).toBe(
      "31 oct 2026, 18:03 UTC",
    );
    expect(formatTime("2026-10-31T14:03:15-04:00", "en")).toBe(
      "31 Oct 2026, 18:03 UTC",
    );
  });
});
