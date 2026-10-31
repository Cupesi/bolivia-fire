import { languageTests } from "./languages";
import { test, expect } from "@playwright/test";
languageTests(true);

test("@smoke localizes synthetic undefined-score verification notes", async ({
  page,
}) => {
  const header =
    "issue_date,n_cells,positives,ap,brier,roc_auc,recall_area_5,recall_area_10,ece,climatology_ap,notes,prospective,generated_utc,publication_lag_hours";
  const row = [
    "2026-10-19",
    "4",
    "0",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "average_precision: single_class; climatology_ap: unavailable or single class; climatology uses department fallback for cells without training cell climatology",
    "false",
    "2026-10-31T06:00:00Z",
    "294",
  ].join(",");
  await page.route("**/data/verification/track_record.csv", (route) =>
    route.fulfill({ status: 200, body: `${header}\n${row}\n` }),
  );
  await page.goto("./?lang=es");
  const track = page.locator(".track-panel");
  await expect(track).toContainText("AP: una sola clase");
  await expect(track).toContainText(
    "AP de climatología: no disponible o una sola clase",
  );
  await expect(track).toContainText(
    "Las celdas sin climatología propia calculada con datos de entrenamiento",
  );
  await expect(track).toContainText("Indefinido");
  await expect(track).not.toContainText(
    /single_class|climatology uses|unavailable or single class/,
  );
});

for (const width of [1440, 390]) {
  test(`@smoke synthetic singular and plural counts stay localized at ${width}px`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    let count = 1;
    await page.route("**/data/forecasts/*.json", async (route) => {
      const response = await route.fetch();
      const forecast = await response.json();
      forecast.input_window.unobserved_days = Array.from(
        { length: count },
        (_, index) =>
          new Date(
            Date.parse(forecast.input_window.first_day + "T00:00:00Z") +
              index * 86400000,
          )
            .toISOString()
            .slice(0, 10),
      );
      await route.fulfill({ response, json: forecast });
    });
    await page.route("**/data/static/cell_places.json", async (route) => {
      const response = await route.fetch();
      const places = await response.json();
      places.municipality[0] = "Unique synthetic municipality";
      await route.fulfill({ response, json: places });
    });
    for (count of [0, 1, 2]) {
      await page.goto("./?lang=en");
      await expect(page.getByTestId("layer-status")).toContainText("Showing");
      for (const language of ["en", "es"] as const) {
        await page
          .locator(`.language-control button[lang="${language}"]`)
          .click();
        await expect(page.locator("html")).toHaveAttribute("lang", language);
        if (count) {
          await expect(
            page.getByTestId("input-history").locator("summary"),
          ).toContainText(
            language === "en"
              ? `${count} input ${count === 1 ? "day" : "days"}`
              : `${count} ${count === 1 ? "día no observado" : "días no observados"}`,
          );
          await page.locator(".forecast-summary").screenshot({
            path: info.outputPath(
              `${width}-${language}-${count}-input-days.png`,
            ),
          });
        } else {
          await expect(page.getByTestId("input-history")).toHaveCount(0);
        }
        await page
          .getByRole("button", {
            name: language === "en" ? "Table view" : "Vista de tabla",
            exact: true,
          })
          .click();
        await page
          .locator("#place-filter")
          .fill("Unique synthetic municipality");
        await expect(page.getByTestId("table-page")).toHaveText(
          language === "en"
            ? "Page 1 of 1 · 1 cell"
            : "Página 1 de 1 · 1 celda",
        );
        await page.locator(".pagination").screenshot({
          path: info.outputPath(
            `${width}-${language}-${count}-single-cell.png`,
          ),
        });
      }
    }
  });
}
