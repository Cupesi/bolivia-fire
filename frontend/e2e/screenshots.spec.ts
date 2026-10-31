import { test, expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { normalizeGrid } from "../src/lib/api";
import { fitGrid, screenPoint } from "../src/lib/projection";

test.skip(
  process.env.CAPTURE_DOCS_MEDIA !== "1",
  "Set CAPTURE_DOCS_MEDIA=1 to capture documentation",
);
const destination = resolve("../docs/assets/screenshots");

async function settled(page: Page) {
  await expect(page.getByTestId("layer-status")).toHaveText(
    /Showing|con datos/,
  );
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator(".series-chart")).toBeVisible();
  await expect(page.locator(".series-chart path")).toHaveAttribute(
    "d",
    /M[\d.]+,[\d.]+/,
  );
  await expect(page.getByText(/^(Loading|Cargando)/)).toHaveCount(0);
  if (await page.locator("canvas").count()) {
    await expect
      .poll(() =>
        page.locator("canvas").evaluate((canvas) => {
          const element = canvas as HTMLCanvasElement;
          const image = element
            .getContext("2d")!
            .getImageData(0, 0, element.width, element.height).data;
          const colors = new Set<string>();
          for (let index = 0; index < image.length; index += 80)
            colors.add(
              `${image[index]},${image[index + 1]},${image[index + 2]}`,
            );
          return colors.size;
        }),
      )
      .toBeGreaterThan(8);
  }
  await page.evaluate(
    () =>
      new Promise<void>((done) =>
        requestAnimationFrame(() => requestAnimationFrame(() => done())),
      ),
  );
}
async function frame(page: Page, selector = ".workspace", margin = 0) {
  await page.evaluate(
    ({ selector, margin }) => {
      const element = document.querySelector(selector)!;
      window.scrollTo(
        0,
        element.getBoundingClientRect().top + scrollY - margin,
      );
    },
    { selector, margin },
  );
  await settled(page);
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
}
async function capture(page: Page, name: string) {
  await settled(page);
  await page.mouse.move(0, 0);
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
  await page.screenshot({ path: resolve(destination, name) });
}
async function selectCell(page: Page, staticMode: boolean, holdout = false) {
  const raw = await (
    await page.request.get(
      new URL(
        staticMode ? "/data/static/grid.json" : "/api/v1/grid",
        page.url(),
      ).href,
    )
  ).json();
  const grid = normalizeGrid(raw);
  const cell = holdout
    ? grid.cells.find(
        (cell) =>
          cell.geoHoldout &&
          cell.department === "Santa Cruz" &&
          cell.municipality,
      )!
    : grid.cells.find((cell) => cell.id === "r1452c2392")!;
  const map = page.locator(".map-canvas");
  const box = (await map.boundingBox())!;
  const point = screenPoint(
    cell.col + 0.5,
    cell.row + 0.5,
    fitGrid(grid.bounds, box.width, box.height),
  );
  await map.click({ position: point });
  await expect(page.getByTestId("selected-cell")).toHaveText(cell.id);
  await settled(page);
}
async function setWeek(page: Page, date: string) {
  await page.getByLabel("Issue week").fill(date);
  await page.getByLabel("Issue week").press("Tab");
  await settled(page);
}

test("capture historical layers and navigation with real prepared data", async ({
  page,
}, info) => {
  test.setTimeout(180_000);
  await mkdir(destination, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/?lang=en");
  await expect(page.getByTestId("layer-status")).toContainText("Showing");
  await page.getByLabel("Issue week").fill("2024-09-02");
  await page.getByLabel("Issue week").press("Tab");
  await expect(page.getByTestId("layer-status")).toContainText("Showing");
  await selectCell(page, false, true);
  await page.getByLabel("From", { exact: true }).fill("2024-01-01");
  await page.getByLabel("To", { exact: true }).fill("2024-12-30");
  await page.getByLabel("To", { exact: true }).press("Tab");
  await frame(page);
  await capture(page, "observed-desktop.png");
  await page
    .getByRole("combobox", { name: /^Model/ })
    .selectOption("strict/lightgbm_08");
  await page
    .getByRole("button", { name: "Retrospective estimate", exact: true })
    .click();
  await expect(page.getByTestId("value-model")).toContainText(
    "strict_geo/lightgbm_04",
  );
  await frame(page);
  await capture(page, "estimate-desktop.png");
  for (let index = 0; index < 4; index++) {
    const date = new Date(Date.UTC(2024, 8, 2 + index * 7))
      .toISOString()
      .slice(0, 10);
    await setWeek(page, date);
    await frame(page);
    await page.screenshot({
      path: info.outputPath(`week-${String(index).padStart(2, "0")}.png`),
    });
  }
});

test("capture live static views in English and Spanish", async ({ page }) => {
  test.setTimeout(180_000);
  await mkdir(destination, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(
    `${process.env.DOCS_STATIC_URL || "http://127.0.0.1:4173"}/?lang=en`,
  );
  await expect(
    page.locator('.language-control button[lang="es"]'),
  ).toBeVisible();
  await expect(page.locator(".language-control")).not.toContainText("|");
  await expect(page.getByTestId("layer-status")).toContainText("Showing");
  await selectCell(page, true);
  await frame(page);
  await capture(page, "live-forecast-desktop.png");
  for (let index = 0; index < 5; index++)
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await frame(page);
  await capture(page, "live-cell-detail.png");
  await page.getByRole("button", { name: "Table view", exact: true }).click();
  await page
    .getByLabel("Filter places", { exact: true })
    .fill("Campesina el Soto");
  await frame(page);
  await capture(page, "table-desktop.png");
  await page.getByRole("button", { name: "Map view", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Reset map extent", exact: true })
    .click();
  await frame(page, ".map-panel", 10);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await capture(page, "estimate-mobile.png");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.locator('.language-control button[lang="es"]').click();
  await expect(page.locator("html")).toHaveAttribute("lang", "es");
  await frame(page);
  await capture(page, "live-spanish-desktop.png");
  await page.locator('.language-control button[lang="en"]').click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await frame(page, ".track-panel", 25);
  await capture(page, "track-record-desktop.png");
});
