import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { normalizeGrid, normalizeWeeks } from "../src/lib/api";
import { fitGrid, screenPoint } from "../src/lib/projection";

async function openExplorer(page: Page) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Weekly fire detections" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Table view", exact: true }),
  ).toBeEnabled();
  await expect(page.getByTestId("layer-status")).toHaveText(/Showing|No cells/);
}

test("initial render uses real artifacts without console or page errors", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await openExplorer(page);
  await expect(
    page.getByRole("application", { name: "Bolivia cell map" }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("desktop.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("week navigation uses available weeks and keyboard", async ({
  page,
  request,
}) => {
  const response = await request.get("/api/v1/weeks?limit=100&offset=0");
  expect(response.ok()).toBe(true);
  const firstPage = await response.json();
  const weeks = normalizeWeeks(firstPage);
  for (let offset = 100; offset < firstPage.total; offset += 100) {
    const next = await request.get(`/api/v1/weeks?limit=100&offset=${offset}`);
    expect(next.ok()).toBe(true);
    weeks.push(...normalizeWeeks(await next.json()));
  }
  expect(weeks.length).toBeGreaterThan(1);
  await openExplorer(page);
  const input = page.getByLabel("Issue week");
  await expect(input).toHaveValue(weeks.at(-1)!.date);
  await page
    .getByRole("button", { name: "Previous week", exact: true })
    .click();
  await expect(input).toHaveValue(weeks.at(-2)!.date);
  await input.press("Alt+ArrowRight");
  await expect(input).toHaveValue(weeks.at(-1)!.date);
  await expect(
    page.getByRole("button", { name: "Next week", exact: true }),
  ).toBeDisabled();
});

test("real outage and complete weeks show consistent observation coverage", async ({
  page,
  request,
}, info) => {
  await openExplorer(page);
  const input = page.getByLabel("Issue week");
  const outageNotice = page.getByTestId("observation-notice");
  const coverageNotice = page.getByTestId("low-coverage-notice");
  for (const [date, complete] of [
    ["2025-12-29", true],
    ["2022-08-01", false],
  ] as const) {
    const response = await request.get(
      `/api/v1/weeks?start=${date}&end=${date}`,
    );
    expect(response.ok()).toBe(true);
    expect((await response.json()).weeks).toEqual([
      {
        issue_date: date,
        label_complete_share: complete ? 1 : 0,
        target_complete: complete,
        outage: true,
        target_low_coverage: !complete,
      },
    ]);
    await input.fill(date);
    await input.press("Tab");
    await expect(page.getByTestId("layer-status")).toHaveText(
      complete
        ? "Showing 37,146 cells · 37,146 with data · 0 missing"
        : "Showing 37,146 cells · 0 with data · 37,146 missing",
    );
    await expect(page.getByLabel("Weekly grid")).toContainText(
      complete ? "100.0% label completeness" : "0.0% label completeness",
    );
    if (complete) {
      await expect(outageNotice).toHaveCount(0);
      await expect(coverageNotice).toHaveCount(0);
    } else {
      await expect(outageNotice).toContainText("unobserved target days");
      await expect(outageNotice).toContainText("missing, never zero");
      await expect(coverageNotice).toContainText(
        "zero detections in the acquisition area",
      );
      await expect(coverageNotice).toContainText(
        "does not determine label completeness",
      );
    }
    await page.screenshot({
      path: info.outputPath(`${complete ? "complete" : "outage"}-week.png`),
      fullPage: true,
    });
  }
});

test("project identity and curated provenance describe the sources used", async ({
  page,
}, info) => {
  await openExplorer(page);
  await expect(
    page
      .locator("header")
      .getByRole("link", { name: "Bolivia Fire", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveTitle(/Weekly fire detections/);
  const about = page.getByRole("region", { name: "About and limitations" });
  await expect(about).toContainText("Revised CHIRPS rainfall covariates");
  await about
    .getByText("Dataset-specific limitations", { exact: true })
    .click();
  await expect(about).not.toContainText("MapBiomas");
  await expect(about.getByRole("link")).toHaveText([
    "NASA FIRMS",
    "geoBoundaries",
    "CHIRPS",
  ]);
  await expect(about.locator(".provenance > dl > div > dt")).toHaveText([
    "Protocol version",
    "Protocol SHA-256",
    "Cells SHA-256",
    "Panel snapshot",
    "Sources SHA-256",
  ]);
  await about.getByText("View identifiers", { exact: true }).click();
  await expect(about.locator(".provenance")).toContainText(
    "FIRMS manifest SHA-256",
  );
  await expect(about.locator(".provenance")).not.toContainText(
    /protocol_sha256|sources_sha256|cells_sha256|firms_manifest_sha256/,
  );
  await page.screenshot({
    path: info.outputPath("provenance.png"),
    fullPage: true,
  });
});

test("estimate layer reflects real availability and states its retrospective meaning", async ({
  page,
}) => {
  await openExplorer(page);
  const estimate = page.getByRole("button", {
    name: "Retrospective estimate",
    exact: true,
  });
  if (await estimate.isEnabled()) {
    await estimate.click();
    await expect(estimate).toHaveAttribute("aria-pressed", "true");
    await expect(
      page.getByText(
        "Retrospective model estimate, not an operational forecast.",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(page.getByTestId("layer-status")).toHaveText(
      /Showing|Missing predictions|No cells|Incompatible filters/,
    );
    await page
      .getByRole("button", { name: "Observed detections", exact: true })
      .click();
    await expect(page.getByTestId("layer-status")).toHaveText(
      /Showing|No cells/,
    );
  } else {
    await expect(
      page.getByText(
        "Missing predictions: no prepared estimates are available.",
        { exact: true },
      ),
    ).toBeVisible();
  }
});

test("selects a real cell with the mouse and the keyboard", async ({
  page,
  request,
}, info) => {
  await openExplorer(page);
  const gridResponse = await request.get("/api/v1/grid");
  expect(gridResponse.ok()).toBe(true);
  const grid = normalizeGrid(await gridResponse.json());
  const cell = grid.cells[Math.floor(grid.cells.length / 2)];
  const canvas = page.getByRole("application", { name: "Bolivia cell map" });
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  const point = screenPoint(
    cell.col + 0.5,
    cell.row + 0.5,
    fitGrid(grid.bounds, box!.width, box!.height),
  );
  // WebKit truncates fractional pointer coordinates; use the nearest pixel to the cell center.
  await page.mouse.click(
    Math.round(box!.x + point.x),
    Math.round(box!.y + point.y),
  );
  await expect(page.getByTestId("selected-cell")).toHaveText(cell.id);
  await canvas.focus();
  await canvas.press("Home");
  await canvas.press("Enter");
  await expect(page.getByTestId("selected-cell")).not.toBeEmpty();
  const centerId = await page.getByTestId("selected-cell").textContent();
  const center = grid.cells.find((candidate) => candidate.id === centerId)!;
  const east = grid.cells
    .filter(
      (candidate) => candidate.row === center.row && candidate.col > center.col,
    )
    .sort((a, b) => a.col - b.col)[0];
  expect(east).toBeDefined();
  await canvas.press("+");
  await canvas.press("Control+ArrowLeft");
  await canvas.press("ArrowRight");
  await canvas.press("Enter");
  await expect(page.getByTestId("selected-cell")).toHaveText(east.id);
  await canvas.hover();
  const beforeZoom = await canvas.screenshot();
  await page.mouse.wheel(0, -600);
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  expect((await canvas.screenshot()).equals(beforeZoom)).toBe(false);
  await page.screenshot({
    path: info.outputPath("selection.png"),
    fullPage: true,
  });
});

test("table alternative sorts, paginates, selects cells and downloads CSV", async ({
  page,
}, info) => {
  await openExplorer(page);
  await page.getByRole("button", { name: "Table view", exact: true }).click();
  await expect(
    page.getByRole("table", { name: "Current week cells" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Sort by department", exact: true })
    .click();
  await expect(
    page.getByRole("columnheader", { name: "Sort by department" }),
  ).toHaveAttribute("aria-sort", "ascending");
  const departments = await page
    .getByRole("table", { name: "Current week cells" })
    .locator("tbody tr td:nth-child(2)")
    .allTextContents();
  expect(departments).toEqual(
    [...departments].sort((a, b) => a.localeCompare(b)),
  );
  await page.getByLabel("Top cells").selectOption("100");
  await page
    .getByRole("button", { name: "Next table page", exact: true })
    .click();
  await expect(page.getByTestId("table-page")).toHaveText(/Page 2/);
  await page
    .getByRole("button", { name: /^Select cell / })
    .first()
    .click();
  await expect(page.getByTestId("selected-cell")).not.toBeEmpty();
  const downloadEvent = page.waitForEvent("download");
  await page
    .getByRole("link", { name: "Download week CSV", exact: true })
    .click();
  const download = await downloadEvent;
  expect(await download.failure()).toBeNull();
  await download.saveAs(info.outputPath(download.suggestedFilename()));
  const csv = await readFile(
    info.outputPath(download.suggestedFilename()),
    "utf8",
  );
  expect(csv).toContain("# project=Bolivia Fire");
  const records = csv
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith("#"));
  expect(records[0]).toBe(
    "cell_id,issue_date,kind,variant,model,value,n_primary",
  );
  expect(records.length).toBeGreaterThan(1);
  const selectedDate = await page.getByLabel("Issue week").inputValue();
  expect(
    records.slice(1).every((line) => line.split(",")[1] === selectedDate),
  ).toBe(true);
  await page.screenshot({ path: info.outputPath("table.png"), fullPage: true });
});

test("API offline has a clear recoverable state", async ({ page }, info) => {
  await page.route("**/api/v1/**", (route) => route.abort("failed"));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("API offline");
  await expect(
    page.getByRole("button", { name: "Retry connection" }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("offline.png"),
    fullPage: true,
  });
  await page.unroute("**/api/v1/**");
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(page.getByTestId("layer-status")).toHaveText(/Showing|No cells/);
});

test("mobile viewport stacks panels without horizontal overflow", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openExplorer(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("application", { name: "Bolivia cell map" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("selected-cell")).not.toBeEmpty();
  await page.screenshot({
    path: info.outputPath("mobile.png"),
    fullPage: true,
  });
});
