import { defineConfig, devices } from "@playwright/test";

/**
 * Browser E2E tests. Start the backend (port 8000) and frontend (port 3000) first,
 * see docs/testing.md. Tests that hit the real Claude API need E2E_OCR_MODE=hybrid
 * (default) and a backend with ANTHROPIC_API_KEY; use E2E_OCR_MODE=local for a free run.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 6 * 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : undefined,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
});
