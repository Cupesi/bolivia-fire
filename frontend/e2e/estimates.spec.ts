import { formatDate, formatNumber } from "../src/lib/i18n";
import { test, expect } from "@playwright/test";

for (const [viewport, width, height] of [
  ["desktop", 1440, 1000],
  ["mobile", 390, 844],
] as const) {
  test(`${viewport} estimates show model identity and calibrated p beside observed y`, async ({
    page,
    request,
  }, info) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await expect(page.getByTestId("layer-status")).toHaveText(/Showing/);
    await page.getByLabel("Issue week").fill("2024-09-02");
    await page.getByLabel("Issue week").press("Tab");
    await page
      .getByRole("combobox", { name: /^Variant/ })
      .selectOption("strict");
    await page
      .getByRole("combobox", { name: /^Model/ })
      .selectOption("strict/lightgbm_08");
    await page
      .getByRole("button", { name: "Retrospective estimate", exact: true })
      .click();
    await expect(page.getByTestId("layer-status")).toHaveText(
      "Showing 29,506 cells · 29,506 with data · 0 missing",
    );
    await expect(
      page.getByText(
        "Retrospective model estimate, not an operational forecast.",
        { exact: true },
      ),
    ).toBeVisible();
    const weekly = page.getByLabel("Weekly grid", { exact: true });
    await expect(weekly).toContainText("Model: strict/lightgbm_08");
    await expect(weekly).toContainText("Variant: strict");
    const legend = page.getByLabel("Map legend");
    await expect(legend).toContainText("Probability");
    await expect(legend.locator(".scale-labels span")).toHaveText([
      "0",
      "0.5",
      "1",
    ]);

    const query = new URLSearchParams({
      issue_date: "2024-09-02",
      kind: "estimate",
      variant: "strict",
      model: "strict/lightgbm_08",
    });
    const response = await request.get(`/api/v1/layer?${query}`);
    expect(response.ok()).toBe(true);
    const layer = await response.json();
    expect(layer.value).toHaveLength(29506);
    expect(
      layer.value.every(
        (p: unknown) => typeof p === "number" && p >= 0 && p <= 1,
      ),
    ).toBe(true);

    await page.getByRole("button", { name: "Table view", exact: true }).click();
    await page
      .getByRole("button", { name: "Sort by value", exact: true })
      .click();
    await page
      .getByRole("button", { name: /^Select cell / })
      .first()
      .click();
    const cell = await page.getByTestId("selected-cell").innerText();
    await page.getByLabel("From", { exact: true }).fill("2024-01-01");
    await page.getByLabel("To", { exact: true }).fill("2024-12-30");
    await page.getByLabel("To", { exact: true }).press("Tab");
    const seriesQuery = new URLSearchParams({
      start: "2024-01-01",
      end: "2024-12-30",
      variant: "strict",
      model: "strict/lightgbm_08",
    });
    const seriesResponse = await request.get(
      `/api/v1/cells/${cell}/series?${seriesQuery}`,
    );
    expect(seriesResponse.ok()).toBe(true);
    const series = await seriesResponse.json();
    expect(series.p.some((p: unknown) => typeof p === "number")).toBe(true);
    expect(series.y.some((y: unknown) => y === 0 || y === 1)).toBe(true);
    const chart = page.getByRole("img", {
      name: "Weekly observed detection indicator y and estimated probability p, from zero to one",
    });
    await expect(chart).toBeVisible();
    await expect(chart.locator("path")).toHaveAttribute("d", /M[\d.]+,[\d.]+/);
    await expect
      .poll(() => chart.locator("circle title").allTextContents())
      .toEqual(
        expect.arrayContaining(
          series.issue_date.flatMap((date: string, index: number) => [
            ...(series.p[index] === null
              ? []
              : [
                  `${formatDate(date, "en")}: p = ${series.p[index].toFixed(3)}`,
                ]),
            ...(series.y[index] === null
              ? []
              : [
                  `${formatDate(date, "en")}: observed y = ${series.y[index]}; detections = ${series.n_primary[index] == null ? "Missing" : formatNumber(series.n_primary[index], "en")}`,
                ]),
          ]),
        ),
      );
    await page.getByText("Read weekly values", { exact: true }).click();
    const rows = page
      .getByRole("table", { name: "Selected cell weekly series" })
      .locator("tbody tr");
    await expect(rows).toHaveCount(series.issue_date.length);
    for (const index of [0, 35, 52]) {
      await expect(rows.nth(index).locator("td")).toHaveText([
        formatDate(series.issue_date[index], "en"),
        String(series.y[index] ?? "Missing"),
        series.n_primary[index] == null
          ? "Missing"
          : formatNumber(series.n_primary[index], "en"),
        series.p[index]?.toFixed(3) ?? "Missing",
      ]);
    }
    await page.getByText("Read weekly values", { exact: true }).click();
    await page.getByRole("button", { name: "Map view", exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath(`${viewport}-estimate-history.png`),
      fullPage: true,
    });

    await page
      .getByRole("combobox", { name: /^Model/ })
      .selectOption("strict_geo/lightgbm_04");
    await expect(page.getByTestId("layer-status")).toHaveText(
      "Showing 7,640 cells · 7,640 with data · 0 missing",
    );
    await expect(weekly).toContainText("Model: strict_geo/lightgbm_04");
  });
}
