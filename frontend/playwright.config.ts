import { defineConfig, devices } from "@playwright/test";

// TC-82 end-to-end against a running stack (API :8000 + web :3000), see e2e/README.md
export default defineConfig({
  testDir: "e2e",
  testMatch: "*.e2e.ts", // not *.spec/test: vitest must not pick these up
  timeout: 120_000,
  fullyParallel: false,
  reporter: [["list"]],
  outputDir: "e2e/.results",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    viewport: { width: 1440, height: 1000 },
    acceptDownloads: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
});
