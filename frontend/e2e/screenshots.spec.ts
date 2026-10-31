import { test, expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

// Opt in so routine browser verification does not overwrite published media.
test.skip(process.env.CAPTURE_DOCS_MEDIA !== "1", "Set CAPTURE_DOCS_MEDIA=1 to capture documentation");

async function settled(page: Page) {
  await expect(page.getByTestId("layer-status")).toHaveText(/Showing|Missing predictions/);
  await page.evaluate(() => new Promise<void>((done) =>
    requestAnimationFrame(() => requestAnimationFrame(() => done()))));
}

async function setWeek(page: Page, date: string) {
  const response = page.waitForResponse((item) =>
    item.url().includes("/api/v1/layer?") &&
    new URL(item.url()).searchParams.get("issue_date") === date);
  await page.getByLabel("Issue week").fill(date);
  await page.getByLabel("Issue week").press("Tab");
  await response;
  await settled(page);
}

async function frameExplorer(page: Page, top = 70) {
  await page.evaluate((top) => {
    const workspace = document.querySelector(".workspace")!;
    window.scrollTo(0, workspace.getBoundingClientRect().top + window.scrollY - top);
  }, top);
  await settled(page);
}

test("capture real-data documentation views and week-navigation frames", async ({ page }, info) => {
  test.setTimeout(180_000);
  const destination = resolve("../docs/assets/screenshots");
  await mkdir(destination, { recursive: true });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await settled(page);
  await setWeek(page, "2024-09-02");
  await frameExplorer(page, 0);
  await page.screenshot({ path: resolve(destination, "observed-desktop.png") });

  await page.getByRole("combobox", { name: /^Variant/ }).selectOption("strict");
  await page.getByRole("combobox", { name: /^Model/ }).selectOption("strict/lightgbm_08");
  await page.getByRole("button", { name: "Retrospective estimate", exact: true }).click();
  await expect(page.getByTestId("layer-status")).toContainText("29,506 with data");
  await page.getByRole("button", { name: "Table view", exact: true }).click();
  await page.getByRole("button", { name: /^Select cell / }).first().click();
  await expect(page.getByTestId("selected-cell")).not.toBeEmpty();
  await page.getByLabel("From", { exact: true }).fill("2024-01-01");
  await page.getByLabel("To", { exact: true }).fill("2024-12-30");
  await page.getByLabel("To", { exact: true }).press("Tab");
  const chart = page.getByRole("img", {
    name: "Weekly observed detection indicator y and estimated probability p, from zero to one",
  });
  await expect(chart.locator("path")).toHaveAttribute("d", /M[\d.]+,[\d.]+/);
  await page.getByRole("button", { name: "Map view", exact: true }).click();
  await frameExplorer(page);
  await page.screenshot({ path: resolve(destination, "estimate-desktop.png") });
  await page.getByRole("button", { name: "Table view", exact: true }).click();
  await frameExplorer(page);
  await page.screenshot({ path: resolve(destination, "table-desktop.png") });

  await page.getByRole("button", { name: "Map view", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("Weekly grid", { exact: true }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await settled(page);
  await page.screenshot({ path: resolve(destination, "estimate-mobile.png") });

  await page.setViewportSize({ width: 1440, height: 900 });
  for (let index = 0; index < 13; index++) {
    const date = new Date(Date.UTC(2024, 7, 5 + index * 7)).toISOString().slice(0, 10);
    await setWeek(page, date);
    await frameExplorer(page);
    await page.screenshot({ path: info.outputPath(`week-${String(index).padStart(2, "0")}.png`) });
  }
  expect(errors).toEqual([]);
});
