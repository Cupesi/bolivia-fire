import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

export function languageTests(staticMode: boolean) {
  test("@smoke language swap keeps data, selection, zoom and canvas pixels", async ({
    page,
  }, info) => {
    const dataRequests: string[] = [];
    const scripts: string[] = [];
    page.on("request", (request) => {
      if (/\/(api\/v1|data)\//.test(request.url()))
        dataRequests.push(request.url());
      if (/\/assets\/.*\.js/.test(request.url())) scripts.push(request.url());
    });
    await page.goto("./?lang=en");
    await expect(page.getByTestId("layer-status")).toContainText("Showing");
    await expect(page.locator("#about")).toBeVisible();
    expect(scripts.some((url) => /\/es-/.test(url))).toBe(false);
    const map = page.locator(".map-canvas");
    await map.focus();
    await map.press("Enter");
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await expect(page.locator(".series-chart")).toBeVisible();
    await page.locator('.language-control button[lang="en"]').focus();
    await page.waitForLoadState("networkidle");
    const selected = await page.getByTestId("selected-cell").textContent();
    const zoom = await map.getAttribute("data-zoom");
    const extent = await map.boundingBox();
    const pixels = await map
      .locator("canvas")
      .evaluate((canvas) => (canvas as HTMLCanvasElement).toDataURL());
    await page.evaluate(() => {
      const prototype = CanvasRenderingContext2D.prototype;
      const original = prototype.drawImage;
      const events: string[] = [];
      Object.defineProperty(window, "canvasDraws", { value: events });
      prototype.drawImage = new Proxy(original, {
        apply(target, context, args) {
          events.push("draw");
          return Reflect.apply(target, context, args);
        },
      });
      performance.clearMeasures("language-swap");
    });
    const before = dataRequests.length;
    await page.locator('.language-control button[lang="es"]').focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await expect(page).toHaveTitle(
      "Bolivia Fire · Detecciones satelitales semanales",
    );
    await expect(
      page.locator('.language-control button[lang="es"]'),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("selected-cell")).toHaveText(selected!);
    await expect(map).toHaveAttribute("data-zoom", zoom!);
    const translatedExtent = await map.boundingBox();
    expect(translatedExtent?.width).toBe(extent?.width);
    expect(translatedExtent?.height).toBe(extent?.height);
    expect(
      await map
        .locator("canvas")
        .evaluate((canvas) => (canvas as HTMLCanvasElement).toDataURL()),
    ).toBe(pixels);
    expect(
      await page.evaluate(() => Reflect.get(window, "canvasDraws")),
    ).toEqual([]);
    await page.locator('.language-control button[lang="en"]').click();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    expect(dataRequests).toHaveLength(before);
    expect(scripts.filter((url) => /\/es-/.test(url))).toHaveLength(1);
    const measurements = await page.evaluate(() =>
      performance
        .getEntriesByName("language-swap")
        .map((entry) => entry.duration),
    );
    expect(measurements).toHaveLength(2);
    expect(Math.max(...measurements)).toBeLessThan(50);
    await info.attach("language-swap.json", {
      body: JSON.stringify({
        mode: staticMode ? "static" : "api",
        measurements,
        dataRequestsDuringSwap: dataRequests.length - before,
        canvasDrawsDuringSwap: await page.evaluate(() =>
          Reflect.get(window, "canvasDraws"),
        ),
      }),
      contentType: "application/json",
    });
  });

  test("@smoke language preference persists and query overrides it", async ({
    page,
  }) => {
    await page.goto("./");
    await page.locator('.language-control button[lang="es"]').click();
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await page.goto("./?lang=en");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await page.goto("./?lang=es");
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await page.locator('.language-control button[lang="en"]').focus();
    await page.keyboard.press("Space");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
  });

  test("@smoke detects Spanish and works when storage is blocked", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "languages", {
        get: () => ["es-BO", "en"],
      });
      Storage.prototype.getItem = () => {
        throw new DOMException("Blocked", "SecurityError");
      };
      Storage.prototype.setItem = () => {
        throw new DOMException("Blocked", "SecurityError");
      };
    });
    await page.goto("./");
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await expect(
      page.getByRole("heading", {
        name: "Detecciones satelitales semanales",
        exact: true,
      }),
    ).toBeVisible();
    await page.locator('.language-control button[lang="en"]').click();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await page.goto("./?lang=en");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
  });

  test("@smoke localizes recoverable offline errors", async ({ page }) => {
    await page.route(staticMode ? "**/data/**" : "**/api/v1/**", (route) =>
      route.abort("failed"),
    );
    await page.goto("./?lang=es");
    await expect(page.getByRole("alert")).toContainText(
      staticMode ? "Archivos de datos no disponibles" : "Sin conexión",
    );
    await expect(
      page.getByRole("button", { name: "Reintentar conexión" }),
    ).toBeVisible();
    await expect(page.getByRole("alert")).not.toContainText(
      /offline|unavailable/,
    );
  });

  for (const [viewport, width, height] of [
    ["desktop", 1440, 1000],
    ["mobile", 390, 844],
  ] as const) {
    test(`@smoke ${viewport} English and Spanish remain readable and export neutral CSV`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width, height });
      await page.goto("./?lang=en");
      await expect(page.getByTestId("layer-status")).toContainText("Showing");
      await expect(page.locator("#about")).toBeVisible();
      const map = page.locator(".map-canvas");
      await map.focus();
      await map.press("Enter");
      await expect(page.locator(".series-chart")).toBeVisible();
      for (const language of ["en", "es"] as const) {
        await page
          .locator(`.language-control button[lang="${language}"]`)
          .click();
        await expect(page.locator("html")).toHaveAttribute("lang", language);
        await expect(page.locator(".cell-facts dd").first()).toHaveText(
          /\d[,.]\d{2}\u00a0km²/,
        );
        await page.locator(".detail-panel").screenshot({
          path: info.outputPath(`${viewport}-${language}-detail.png`),
        });
        for (const detail of await page.locator("#about details").all()) {
          if ((await detail.getAttribute("open")) === null) {
            await detail.locator(":scope > summary").click();
          }
        }
        await page.locator("#about .about-grid").screenshot({
          path: info.outputPath(`${viewport}-${language}-caveats.png`),
        });
        await page.locator("#about").screenshot({
          path: info.outputPath(`${viewport}-${language}-about.png`),
        });
        if (staticMode) {
          await page.locator(".track-panel").screenshot({
            path: info.outputPath(`${viewport}-${language}-verification.png`),
          });
        } else {
          const variants = page.locator("#variant");
          const original = await variants.inputValue();
          for (const value of await variants
            .locator("option:not([disabled])")
            .evaluateAll((options) =>
              options.map((option) => (option as HTMLOptionElement).value),
            )) {
            await variants.selectOption(value);
            expect(
              await variants.evaluate((element) => {
                const select = element as HTMLSelectElement;
                const style = getComputedStyle(select);
                const context = document
                  .createElement("canvas")
                  .getContext("2d")!;
                context.font = style.font;
                return (
                  context.measureText(select.selectedOptions[0].text).width +
                    parseFloat(style.paddingLeft) +
                    parseFloat(style.paddingRight) +
                    24 <=
                  select.clientWidth
                );
              }),
              `${language}: ${value} fits at ${width}px`,
            ).toBe(true);
            await page.locator(".filter-bar").screenshot({
              path: info.outputPath(
                `${viewport}-${language}-${value}-variant.png`,
              ),
            });
          }
          await variants.selectOption(original);
          await expect(page.getByTestId("layer-status")).toContainText(
            language === "es" ? "con datos" : "Showing",
          );
        }
        expect(
          await page.locator("button").evaluateAll((buttons) =>
            buttons
              .filter((button) => {
                const box = button.getBoundingClientRect();
                return (
                  box.width &&
                  box.height &&
                  (button.scrollWidth > button.clientWidth ||
                    button.scrollHeight > button.clientHeight)
                );
              })
              .map((button) => button.textContent?.trim()),
          ),
        ).toEqual([]);
        await page.evaluate(() => scrollTo(0, 0));
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        ).toBe(true);
        await page.screenshot({
          path: info.outputPath(`${viewport}-${language}-map.png`),
        });
        const about = page.locator("#about");
        if (language === "es") {
          await expect(about).toContainText(
            "Las detecciones no equivalen a incendios",
          );
          await expect(about).toContainText("Un cero no demuestra ausencia");
          await expect(about).toContainText("GeoNames");
          await expect(about).not.toContainText("NRT has no type field");
        }
        await page
          .getByRole("button", {
            name: language === "es" ? "Vista de tabla" : "Table view",
            exact: true,
          })
          .click();
        const table = page.locator(".cell-table");
        await expect(table).toBeVisible();
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        ).toBe(true);
        await table.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: info.outputPath(`${viewport}-${language}-table.png`),
        });
        const areas = await table
          .locator('tbody [data-column="area"]')
          .allTextContents();
        expect(areas.length).toBeGreaterThan(0);
        expect(
          areas.every((value) =>
            language === "es" ? /,\d{2}$/.test(value) : /\.\d{2}$/.test(value),
          ),
        ).toBe(true);
        await page.locator("#place-filter").fill("zzzz-unmatched-place");
        if (language === "es") {
          await page.locator("#cell-sort").selectOption("area");
          await page.locator('.language-control button[lang="en"]').click();
          await expect(page.locator("html")).toHaveAttribute("lang", "en");
          await expect(page.locator("#place-filter")).toHaveValue(
            "zzzz-unmatched-place",
          );
          await expect(page.locator("#cell-sort")).toHaveValue("area");
          await page.locator('.language-control button[lang="es"]').click();
          await expect(page.locator("html")).toHaveAttribute("lang", "es");
          await expect(page.locator("#place-filter")).toHaveValue(
            "zzzz-unmatched-place",
          );
          await expect(page.locator("#cell-sort")).toHaveValue("area");
        }
        await expect(table.getByRole("status").first()).toContainText(
          language === "es" ? "Ninguna celda coincide" : "No cells match",
        );
        const [download] = await Promise.all([
          page.waitForEvent("download"),
          page
            .getByRole("link", {
              name:
                language === "es"
                  ? "Descargar CSV de la semana"
                  : "Download week CSV",
              exact: true,
            })
            .click(),
        ]);
        const path = info.outputPath(`${language}.csv`);
        await download.saveAs(path);
        const csv = await readFile(path, "utf8");
        const records = csv
          .split(/\r?\n/)
          .filter((line) => line && !line.startsWith("#"));
        expect(records[0]).toBe(
          "cell_id,issue_date,kind,variant,model,value,n_primary,municipality,province,department,nearest_place_name,nearest_place_distance_km,national_area_km2",
        );
        const row = records[1].split(",");
        expect(row.at(-1)).toMatch(/^\d+(\.\d+)?$/);
        if (language === "es")
          expect(csv).toBe(await readFile(info.outputPath("en.csv"), "utf8"));
        await page.locator("#place-filter").fill("");
        await page
          .getByRole("button", {
            name: language === "es" ? "Vista de mapa" : "Map view",
            exact: true,
          })
          .click();
        await page.evaluate(() => scrollTo(0, 0));
      }
    });
  }
}
