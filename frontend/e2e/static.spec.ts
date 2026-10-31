import { expect, test } from "@playwright/test";

test("@smoke serves the latest forecast and cell history under the Pages base path", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  const requests: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => requests.push(request.url()));
  await page.goto("./");
  await expect(
    page.getByText("Experimental weekly estimate (near-real-time data)", {
      exact: true,
    }),
  ).toBeVisible();
  const catalog = await (await page.request.get("data/index.json")).json();
  await expect(page.getByLabel("Issue week")).toHaveValue(catalog.latest_issue);
  await expect(page.getByLabel("Forecast metadata")).toContainText(
    catalog.latest_issue,
  );
  const forecast = await (
    await page.request.get(`data/forecasts/${catalog.latest_issue}.json`)
  ).json();
  await expect(page.getByLabel("Forecast metadata")).toContainText(
    forecast.generated_utc,
  );
  await expect(page.getByLabel("Forecast metadata")).toContainText(
    `${forecast.publication_lag_hours.toFixed(1)} h after issue`,
  );
  await expect(page.getByTestId("layer-status")).toContainText("Showing");
  await expect(page.locator("canvas")).toBeVisible();
  await page.getByRole("button", { name: "Table view" }).click();
  await page
    .getByRole("button", { name: /^Select cell / })
    .first()
    .click();
  await expect(page.getByTestId("selected-cell")).toBeVisible();
  await expect(
    page.getByRole("img", { name: /Weekly observed detection/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Track record", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".about-grid > div > p")
      .filter({ hasText: /The model was trained on standard-processing/ }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("desktop.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
  const origin = new URL(page.url()).origin;
  expect(
    requests.every(
      (url) => new URL(url).origin === origin && !url.includes("/api/"),
    ),
  ).toBe(true);
});

test("@smoke mobile layout keeps the map, history and track record within the page", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("./");
  await expect(page.getByTestId("layer-status")).toContainText("Showing");
  const map = page.getByRole("application", { name: "Bolivia cell map" });
  await map.focus();
  await map.press("Enter");
  await expect(page.getByTestId("selected-cell")).toBeVisible();
  await expect(
    page.getByRole("img", { name: /Weekly observed detection/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Track record", exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.screenshot({
    path: testInfo.outputPath("mobile.png"),
    fullPage: true,
  });
});

test("switches synthetic weeks and renders complete observations and verification scores", async ({
  page,
}) => {
  await page.goto("./");
  await page.getByLabel("Issue week").selectOption("2026-10-19");
  await expect(page.getByLabel("Forecast metadata")).toContainText(
    "2026-10-19",
  );
  await page
    .getByRole("button", { name: "Observed detections", exact: true })
    .click();
  await expect(page.getByTestId("layer-status")).toContainText("4 with data");
  await page.getByRole("button", { name: "Table view" }).click();
  const scores = page.getByRole("table", {
    name: "Weekly verification scores",
  });
  await expect(scores.getByRole("row")).toHaveCount(2);
  await expect(
    scores.getByRole("row").filter({ hasText: "2026-10-19" }),
  ).toContainText("0.063");
  await expect(
    page.getByRole("img", { name: /Weekly average precision/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Previous week" }).click();
  await expect(page.getByLabel("Issue week")).toHaveValue("2026-10-12");
  await expect(
    page.getByRole("button", { name: "Weekly estimate", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Forecast metadata")).toContainText(
    "Backfill estimate",
  );
  const backfill = page.getByRole("table", {
    name: "Backfill verification scores",
  });
  await expect(backfill).toContainText("2026-10-12");
  await expect(scores).not.toContainText("2026-10-12");
  await expect(
    page.getByRole("img", { name: /Weekly average precision/ }),
  ).not.toContainText("2026-10-12");
});

test("keeps an incomplete synthetic observation week unavailable instead of showing zeros", async ({
  page,
}) => {
  await page.goto("./");
  await expect(page.getByTestId("observation-notice")).toContainText(
    "2026-11-01",
  );
  await expect(
    page.getByRole("button", { name: "Observed detections", exact: true }),
  ).toBeDisabled();
  await expect(page.getByTestId("layer-status")).toContainText("1 missing");
});

test("keeps backfill-only verification out of the prospective performance chart", async ({
  page,
}) => {
  await page.route("**/data/verification/track_record.csv", async (route) => {
    const response = await route.fetch();
    const csv = (await response.text()).replace(
      ",true,2026-10-19T06:12:00Z,6.2",
      ",false,2026-10-31T06:00:00Z,294",
    );
    await route.fulfill({ response, body: csv });
  });
  await page.goto("./");
  await expect(
    page.getByText("No prospective verified weeks yet."),
  ).toBeVisible();
  await expect(
    page
      .getByRole("table", { name: "Backfill verification scores" })
      .getByRole("row"),
  ).toHaveCount(3);
  await expect(
    page.getByRole("table", {
      name: "Weekly verification scores",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("img", { name: /Weekly average precision/ }),
  ).toHaveCount(0);
});

test("reports a missing forecast file and recovers on retry", async ({
  page,
}) => {
  await page.route("**/data/forecasts/2026-10-26.json", (route) =>
    route.fulfill({ status: 404, body: "missing" }),
  );
  await page.goto("./");
  await expect(
    page.getByRole("alert").filter({ hasText: "2026-10-26.json" }),
  ).toBeVisible();
  await page.unroute("**/data/forecasts/2026-10-26.json");
  await page.getByRole("button", { name: "Retry weekly data" }).click();
  await expect(page.getByTestId("layer-status")).toContainText(
    "Showing 4 cells",
  );
});

test("reports offline data files and retries the initial load", async ({
  page,
}) => {
  await page.route("**/data/**", (route) =>
    route.abort("internetdisconnected"),
  );
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "Data files offline" }),
  ).toBeVisible();
  await page.unroute("**/data/**");
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(page.getByTestId("layer-status")).toContainText(
    "Showing 4 cells",
  );
});

test("shows the empty track record and a separate missing-file state", async ({
  page,
}) => {
  const header =
    "issue_date,n_cells,positives,ap,brier,roc_auc,recall_area_5,recall_area_10,ece,climatology_ap,notes,prospective,generated_utc,publication_lag_hours\n";
  await page.route("**/data/verification/track_record.csv", (route) =>
    route.fulfill({ status: 200, body: header }),
  );
  await page.goto("./");
  await expect(page.getByText("No verified weeks yet.")).toBeVisible();
  await page.unroute("**/data/verification/track_record.csv");
  await page.route("**/data/verification/track_record.csv", (route) =>
    route.fulfill({ status: 404, body: "missing" }),
  );
  await page.reload();
  await expect(
    page.getByRole("alert").filter({ hasText: "track_record.csv" }),
  ).toBeVisible();
  await page.unroute("**/data/verification/track_record.csv");
  await page.getByRole("button", { name: "Retry track record" }).click();
  await expect(
    page.getByRole("table", { name: "Weekly verification scores" }),
  ).toBeVisible();
});
