import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  outputDir: "artifacts/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  expect: { timeout: 12000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: "artifacts/e2e-report", open: "never" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:8776",
    viewport: { width: 1540, height: 1050 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: { args: ["--disable-dev-shm-usage"] },
  },
  webServer: {
    command:
      "uv run --frozen python -m cytoforge --port 8776 --data-dir .tmp/e2e-data",
    url: "http://127.0.0.1:8776/api/health",
    timeout: 60000,
    reuseExistingServer: false,
  },
});
