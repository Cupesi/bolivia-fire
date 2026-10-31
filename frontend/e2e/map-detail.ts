import { expect, type Page, type TestInfo } from "@playwright/test";
import { normalizeGrid } from "../src/lib/api";
import { decodePlaces } from "../src/lib/static";
import { fitGrid, screenPoint, zoomAt } from "../src/lib/projection";

export async function inspectMapDetail(
  page: Page,
  testInfo: TestInfo,
  staticMode: boolean,
) {
  const gridRaw = await (
    await page.request.get(
      staticMode ? "data/static/grid.json" : "/api/v1/grid",
    )
  ).json();
  const placesRaw = staticMode
    ? await (await page.request.get("data/static/cell_places.json")).json()
    : gridRaw;
  const grid = normalizeGrid({ ...gridRaw, ...decodePlaces(placesRaw) });
  const map = page.getByRole("application", { name: "Bolivia cell map" });
  const canvas = map.locator("canvas");
  await expect(canvas).toBeVisible();
  await canvas.scrollIntoViewIfNeeded();
  await expect(map).toHaveAttribute("data-boundary-levels", "0,1");
  const bounds = (await canvas.boundingBox())!;
  const fitted = fitGrid(grid.bounds, bounds.width, bounds.height);
  const candidates = grid.cells.filter(
    (cell) =>
      cell.municipality &&
      cell.province &&
      cell.nearestPlace &&
      cell.populatedPlaces?.some((place) => place.population >= 20000),
  );
  expect(candidates.length).toBeGreaterThan(0);
  const cell = candidates.sort((a, b) => {
    const distance = (cell: typeof a) => {
      const point = screenPoint(cell.col + 0.5, cell.row + 0.5, fitted);
      return (
        (point.x - bounds.width / 2) ** 2 + (point.y - bounds.height / 2) ** 2
      );
    };
    return distance(a) - distance(b);
  })[0];
  const point = screenPoint(cell.col + 0.5, cell.row + 0.5, fitted);
  const x = Math.round(bounds.x + point.x),
    y = Math.round(bounds.y + point.y);
  await page.mouse.move(x, y);
  await page.mouse.wheel(0, -500);
  await expect(map).toHaveAttribute("data-boundary-levels", "0,1,2");
  await page.mouse.wheel(0, -500);
  await expect(map).toHaveAttribute("data-boundary-levels", "0,1,2,3");
  const zoomed = zoomAt(fitted, Math.exp(1.5), x - bounds.x, y - bounds.y);
  const center = screenPoint(cell.col + 0.5, cell.row + 0.5, zoomed);
  await page.mouse.move(
    Math.round(bounds.x + center.x),
    Math.round(bounds.y + center.y),
  );
  const tooltip = page.getByRole("tooltip");
  await expect(tooltip).toContainText(cell.id);
  await expect(tooltip).toContainText(`Municipality: ${cell.municipality}`);
  await expect(tooltip).toContainText(`Province: ${cell.province}`);
  await expect(tooltip).toContainText(`Department: ${cell.department}`);
  await expect(tooltip).toContainText(
    `Nearest place: ${cell.nearestPlace!.name}`,
  );
  for (const place of cell.populatedPlaces ?? [])
    await expect(tooltip).toContainText(place.name);
  await expect(map.locator(".town-label").first()).toBeVisible();
  const boxes = await map.locator(".town-label").evaluateAll((nodes) =>
    nodes.map((node) => {
      const box = node.getBoundingClientRect();
      return { x: box.x, y: box.y, right: box.right, bottom: box.bottom };
    }),
  );
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i],
        b = boxes[j];
      expect(
        a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y,
      ).toBe(false);
    }
  await page.mouse.click(
    Math.round(bounds.x + center.x),
    Math.round(bounds.y + center.y),
  );
  await expect(page.getByTestId("selected-cell")).toHaveText(cell.id);
  const detail = page.getByRole("complementary", { name: "Cell detail" });
  await expect(detail).toContainText(cell.municipality!);
  await expect(detail).toContainText(cell.province!);
  await expect(detail).toContainText(
    `Nearest place: ${cell.nearestPlace!.name}`,
  );
  await expect(
    page.getByRole("img", { name: /Weekly observed detection/ }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("municipalities-labels-places.png"),
    fullPage: true,
  });
  await page.mouse.move(bounds.x - 5, bounds.y - 5);
  await expect(tooltip).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("municipalities-labels.png"),
    fullPage: true,
  });
}
