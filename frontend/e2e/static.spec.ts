import { expect, test } from "@playwright/test";
import { inspectMapDetail } from "./map-detail";

test("@smoke displays places, zoomed municipality outlines and nonoverlapping town labels", async ({
  page,
}, testInfo) => {
  await page.goto("./");
  await expect(page.getByTestId("layer-status")).toContainText("Showing");
  await expect(page.locator(".provenance")).not.toContainText("Not supplied");
  await expect(page.locator("#about")).toContainText(
    "Place names: GeoNames (CC BY 4.0)",
  );
  await expect(page.locator("#about")).toContainText("geoBoundaries ADM2/ADM3");
  await expect(page.locator("#about")).not.toContainText("CHIRPS");
  const meta = await (await page.request.get("data/static/meta.json")).json();
  for (const value of [
    meta.protocol?.version,
    meta.protocol?.sha256,
    meta.experiments_sha256,
    meta.cells_sha256,
    meta.sources_sha256,
    meta.source_model_sha256,
    meta.source_calibrator_sha256,
    meta.primary_opening_utc,
  ]) {
    if (value) await expect(page.locator(".provenance")).toContainText(value);
  }
  await inspectMapDetail(page, testInfo, true);
});

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
    new Date(forecast.generated_utc)
      .toISOString()
      .slice(0, 16)
      .replace("T", " ") + " UTC",
  );
  await expect(page.getByLabel("Forecast metadata")).toContainText(
    `${Math.round(forecast.publication_lag_hours)} h after issue`,
  );
  const metadata = await (
    await page.request.get("data/static/meta.json")
  ).json();
  if (metadata.model_name && metadata.model_id === forecast.model_id)
    await expect(page.getByLabel("Forecast metadata")).toContainText(
      `${forecast.variant} / ${metadata.model_name}`,
    );
  await expect(
    page
      .getByLabel("Forecast metadata")
      .locator("[title]")
      .filter({ hasText: forecast.model_id.slice(0, 8) }),
  ).toHaveAttribute("title", forecast.model_id);
  const grid = page.getByRole("region", { name: "Weekly grid", exact: true });
  await expect(
    grid.locator("[title]").filter({ hasText: forecast.model_id.slice(0, 8) }),
  ).toHaveText(forecast.model_id.slice(0, 8));
  if (metadata.model_name && metadata.model_id === forecast.model_id)
    await expect(grid).toContainText(
      `${forecast.variant} / ${metadata.model_name}`,
    );
  const observations = await (
    await page.request.get(`data/observations/${catalog.latest_issue}.json`)
  ).json();
  const snapshotDay = new Date(catalog.last_updated_utc)
    .toISOString()
    .slice(0, 10);
  if (observations.unobserved_days.some((day: string) => day >= snapshotDay)) {
    await expect(page.getByTestId("target-progress")).toContainText(
      "Target week in progress:",
    );
    if (observations.unobserved_days.every((day: string) => day >= snapshotDay))
      await expect(page.getByTestId("observation-notice")).toHaveCount(0);
  }
  const inputDetails = page.getByTestId("input-history");
  if (forecast.input_window.unobserved_days.length) {
    await expect(inputDetails.locator("summary")).toHaveText(
      `${forecast.input_window.unobserved_days.length} input days in the 365-day window were unobserved (sensor outages)`,
    );
    await expect(inputDetails).not.toHaveAttribute("open", "");
    await inputDetails.locator("summary").click();
    for (const day of forecast.input_window.unobserved_days)
      await expect(inputDetails).toContainText(day);
    await page.screenshot({
      path: testInfo.outputPath("desktop-input-details.png"),
      fullPage: true,
    });
    await inputDetails.locator("summary").click();
  }
  await page.getByTestId("forecast-provenance").locator("summary").click();
  await expect(page.getByTestId("forecast-provenance")).toContainText(
    `[${forecast.target_start}, ${forecast.target_end}) UTC`,
  );
  await expect(page.getByTestId("forecast-provenance")).toContainText(
    forecast.generated_utc,
  );
  await page.getByTestId("forecast-provenance").locator("summary").click();
  await expect(page.getByTestId("layer-status")).toContainText("Showing");
  await expect(page.locator("canvas")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("desktop-overview.png"),
    fullPage: true,
  });
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

test("keeps a synthetic target week in progress informational while labels remain missing", async ({
  page,
}) => {
  await page.goto("./");
  await expect(page.getByTestId("target-progress")).toHaveText(
    "Target week in progress: observations available through 2026-10-30",
  );
  await expect(page.getByTestId("target-progress")).not.toHaveClass(/notice/);
  await expect(page.getByTestId("observation-notice")).toHaveCount(0);
  await expect(page.getByLabel("Forecast metadata")).toContainText(
    "26 Oct – 1 Nov 2026 (UTC)",
  );
  await expect(
    page.getByRole("button", { name: "Observed detections", exact: true }),
  ).toBeDisabled();
  await expect(page.getByTestId("layer-status")).toContainText("1 missing");
});

test("distinguishes synthetic past outages from pending days and expands input outage dates", async ({
  page,
}) => {
  await page.route("**/data/observations/2026-10-26.json", async (route) => {
    const response = await route.fetch();
    const raw = await response.json();
    raw.unobserved_days.unshift("2026-10-28");
    await route.fulfill({ response, json: raw });
  });
  await page.route("**/data/forecasts/2026-10-26.json", async (route) => {
    const response = await route.fetch();
    const raw = await response.json();
    raw.input_window.unobserved_days = ["2026-10-13", "2026-10-14"];
    await route.fulfill({ response, json: raw });
  });
  await page.goto("./");
  await expect(page.getByTestId("target-progress")).toContainText(
    "observations available through 2026-10-30",
  );
  const notice = page.getByTestId("observation-notice");
  await expect(notice).toContainText(
    "Unobserved target days (sensor outages): 2026-10-28",
  );
  await expect(notice).not.toContainText("2026-10-31");
  await expect(notice).not.toContainText("2026-11-01");
  const details = page.getByTestId("input-history");
  await expect(details.locator("summary")).toHaveText(
    "2 input days in the 365-day window were unobserved (sensor outages)",
  );
  await expect(
    details.getByText("2026-10-13", { exact: true }),
  ).not.toBeVisible();
  await details.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(details.getByText("2026-10-13", { exact: true })).toBeVisible();
  await expect(details.getByText("2026-10-14", { exact: true })).toBeVisible();
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
