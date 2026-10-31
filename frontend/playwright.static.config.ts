import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const base = process.env.PAGES_BASE || "/bolivia-fire/";
const origin = "http://127.0.0.1:4174";
export default defineConfig({
  testDir: "./e2e",
  testMatch: "static.spec.ts",
  outputDir: "./test-results/static",
  workers: 1,
  retries: 0,
  timeout: 45000,
  forbidOnly: true,
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile:
          process.env.STATIC_REPORT || "test-results/static/results.json",
      },
    ],
  ],
  use: {
    baseURL: origin + base,
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
  webServer: {
    command:
      "npm run build:static && npm run preview -- --mode static --host 127.0.0.1 --port 4174 --strictPort",
    url: origin + base,
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      LIVE_DATA_DIR:
        process.env.LIVE_DATA_DIR || resolve("e2e/fixtures/live-data"),
      PAGES_BASE: base,
    },
  },
});
