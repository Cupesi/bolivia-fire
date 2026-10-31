import { expect, type Page, type TestInfo } from "@playwright/test";
import { readFile } from "node:fs/promises";

export async function inspectCellTable(
  page: Page,
  info: TestInfo,
  staticMode = false,
) {
  const source = await (
    await page.request.get(
      staticMode ? "data/static/cell_places.json" : "/api/v1/grid",
    )
  ).json();
  await page.getByRole("button", { name: "Table view", exact: true }).click();
  const table = page.getByRole("table", { name: "Selected week cells" });
  await page.getByLabel("Top cells").selectOption("all");
  const filter = page.getByRole("searchbox", { name: "Filter places" });
  await filter.fill("Concepción");
  const rows = table.locator("tbody tr");
  await expect(rows.first()).toContainText("Concepción");
  await filter.fill("no-such-place-xyz");
  await expect(rows).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: /No cells match/ }),
  ).toBeVisible();
  await filter.fill("Concepción");
  const first = rows.first();
  const id = await first.getAttribute("data-cell-id");
  await first.click();
  await expect(page.getByTestId("selected-cell")).toHaveText(id!);
  await first.getByRole("button", { name: /^Select cell/ }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(rows.nth(1).getByRole("button")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("selected-cell")).toHaveText(
    (await rows.nth(1).getAttribute("data-cell-id"))!,
  );
  for (const column of [
    "id",
    "municipality",
    "province",
    "department",
    "nearestPlace",
    "area",
    "value",
  ]) {
    await page
      .getByRole("combobox", { name: "Sort cells by", exact: true })
      .selectOption(column);
    const header = table.locator(`th[data-column="${column}"]`);
    await expect(header).toHaveAttribute("aria-sort", /ascending|descending/);
    const direction = await header.getAttribute("aria-sort");
    await page.getByRole("button", { name: "Reverse cell sort" }).click();
    await expect(header).toHaveAttribute(
      "aria-sort",
      direction === "ascending" ? "descending" : "ascending",
    );
  }
  for (const column of ["area", "value"]) {
    await expect(
      table.locator(`tbody tr:first-child [data-column="${column}"]`),
    ).toHaveCSS("text-align", "right");
    await expect(
      table.locator(`tbody tr:first-child [data-column="${column}"]`),
    ).toHaveCSS("font-variant-numeric", "tabular-nums");
    await expect(table.locator(`th[data-column="${column}"] button`)).toHaveCSS(
      "text-align",
      "right",
    );
  }
  await expect(first.locator('[data-column="area"]')).toHaveText(
    /^\d+\.\d{2}$/,
  );
  await expect(first.locator('[data-column="value"]')).toHaveText(
    /^\d\.\d{3}$|^Missing$/,
  );
  const downloadEvent = page.waitForEvent("download");
  await page
    .getByRole("link", { name: "Download week CSV", exact: true })
    .click();
  const download = await downloadEvent;
  expect(await download.failure()).toBeNull();
  await download.saveAs(info.outputPath("places.csv"));
  const csv = await readFile(info.outputPath("places.csv"), "utf8");
  expect(csv).toContain(
    "municipality,province,department,nearest_place_name,nearest_place_distance_km,national_area_km2",
  );
  const sourceIndex = source.cell_id.indexOf(
    await first.getAttribute("data-cell-id"),
  );
  expect(sourceIndex).toBeGreaterThanOrEqual(0);
  const province = source.province[sourceIndex];
  expect(csv.includes("Concepción")).toBe(true);
  expect(csv.includes(province)).toBe(true);
  await expect(first.locator('[data-column="province"]')).toHaveText(province);
  await table.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("table-desktop.png"),
    fullPage: true,
  });
  await page
    .locator(".cell-table")
    .evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({
    path: info.outputPath("table-desktop-viewport.png"),
  });
  const nearest = await first
    .locator('[data-column="nearestPlace"]')
    .textContent();
  await page.setViewportSize({ width: 390, height: 844 });
  await table.scrollIntoViewIfNeeded();
  await expect(first.locator(".place-secondary")).toContainText(province);
  await expect(first.locator(".place-secondary")).toContainText(nearest!);
  await expect(table.locator('th[data-column="province"]')).toBeHidden();
  expect(
    await table.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("table-mobile.png"),
    fullPage: true,
  });
  await page
    .locator(".cell-table")
    .evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: info.outputPath("table-mobile-viewport.png") });
}

export async function inspectObservedTable(
  page: Page,
  info: TestInfo,
  expected: Map<string, { detected: number | null; count: number | null }>,
) {
  await page.getByRole("button", { name: "Table view", exact: true }).click();
  const table = page.getByRole("table", { name: "Selected week cells" });
  await page
    .getByRole("combobox", { name: "Sort cells by", exact: true })
    .selectOption("count");
  const rows = table.locator("tbody tr");
  for (let index = 0; index < (await rows.count()); index++) {
    const row = rows.nth(index);
    const source = expected.get((await row.getAttribute("data-cell-id"))!);
    expect(source).toBeDefined();
    await expect(row.locator('[data-column="value"]')).toHaveText(
      String(source!.detected ?? "Missing"),
    );
    await expect(row.locator('[data-column="count"]')).toHaveText(
      String(source!.count ?? "Missing"),
    );
  }
  await expect(table.locator('th[data-column="count"]')).toHaveAttribute(
    "aria-sort",
    "descending",
  );
  await expect(table.locator('th[data-column="count"] button')).toHaveCSS(
    "text-align",
    "right",
  );
  await expect(rows.first().locator('[data-column="count"]')).toHaveCSS(
    "font-variant-numeric",
    "tabular-nums",
  );
  await page
    .locator(".cell-table")
    .evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({
    path: info.outputPath("observed-table-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(table.locator('th[data-column="province"]')).toBeHidden();
  expect(
    await table.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page
    .locator(".cell-table")
    .evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: info.outputPath("observed-table-mobile.png") });
}
