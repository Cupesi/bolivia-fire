import { expect, test } from "@playwright/test";

const description =
  "Explore weekly satellite fire detections and retrospective model estimates for Bolivia.";
const site = "https://bolivia-fire.cupesi.org/";

test("@smoke exposes site icons and social preview metadata", async ({
  page,
}) => {
  await page.goto("./");
  for (const [selector, value] of [
    ['meta[name="description"]', description],
    ['meta[property="og:type"]', "website"],
    ['meta[property="og:title"]', "Bolivia Fire"],
    ['meta[property="og:description"]', description],
    ['meta[property="og:url"]', site],
    ['meta[property="og:image"]', `${site}og-image.png`],
    ['meta[property="og:image:width"]', "1200"],
    ['meta[property="og:image:height"]', "630"],
    [
      'meta[property="og:image:alt"]',
      "Cupesí Labs — Bolivia Fire: Weekly satellite fire detections in Bolivia",
    ],
    ['meta[name="twitter:card"]', "summary_large_image"],
    ['meta[name="twitter:title"]', "Bolivia Fire"],
    ['meta[name="twitter:description"]', description],
    ['meta[name="twitter:image"]', `${site}og-image.png`],
  ]) {
    await expect(page.locator(`head ${selector}`)).toHaveAttribute(
      "content",
      value,
    );
  }
  await expect(page.locator('head meta[name="theme-color"]')).toHaveAttribute(
    "content",
    /^#[0-9a-f]{6}$/,
  );
  for (const [selector, filename] of [
    ['link[rel="icon"][sizes="any"]', "favicon.ico"],
    ['link[rel="icon"][type="image/png"][sizes="32x32"]', "favicon-32.png"],
    ['link[rel="apple-touch-icon"][sizes="180x180"]', "apple-touch-icon.png"],
    ['link[rel="manifest"]', "site.webmanifest"],
  ]) {
    const link = page.locator(`head ${selector}`);
    await expect(link).toHaveAttribute("href", new RegExp(`${filename}$`));
    const url = await link.evaluate((element: HTMLLinkElement) => element.href);
    expect(new URL(url).origin).toBe(new URL(page.url()).origin);
    expect((await page.request.get(url)).status()).toBe(200);
  }
});

test("@smoke serves icon and preview files with their media types and dimensions", async ({
  page,
}) => {
  const favicon = await page.request.get("favicon.ico");
  expect(favicon.status()).toBe(200);
  expect(favicon.headers()["content-type"]).toMatch(
    /^image\/(?:x-icon|vnd\.microsoft\.icon)(?:;|$)/,
  );
  const ico = await favicon.body();
  expect(ico.readUInt16LE(2)).toBe(1);
  expect(ico.readUInt16LE(4)).toBe(3);
  expect([6, 22, 38].map((offset) => [ico[offset], ico[offset + 1]])).toEqual([
    [16, 16],
    [32, 32],
    [48, 48],
  ]);
  for (const [filename, width, height] of [
    ["favicon-32.png", 32, 32],
    ["apple-touch-icon.png", 180, 180],
    ["icon-192.png", 192, 192],
    ["icon-512.png", 512, 512],
    ["og-image.png", 1200, 630],
  ] as const) {
    const response = await page.request.get(filename);
    expect(response.status(), filename).toBe(200);
    expect(response.headers()["content-type"], filename).toMatch(/^image\/png/);
    const png = await response.body();
    expect(png.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([
      width,
      height,
    ]);
    if (filename === "og-image.png") expect(png.length).toBeLessThan(300_000);
  }
  const response = await page.request.get("site.webmanifest");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toMatch(
    /^application\/manifest\+json/,
  );
  const manifest = await response.json();
  expect(manifest).toMatchObject({
    name: "Bolivia Fire",
    short_name: "Bolivia Fire",
    background_color: "#ffffff",
    display: "standalone",
    icons: [
      {
        src: "icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any maskable",
      },
      {
        src: "icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any maskable",
      },
    ],
  });
  await page.goto("./");
  await expect(page.locator('head meta[name="theme-color"]')).toHaveAttribute(
    "content",
    manifest.theme_color,
  );
});
