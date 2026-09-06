import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./webchat-service/tests/e2e",
  timeout: 30_000,
  use: { baseURL: "http://localhost:4173", trace: "retain-on-failure" },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
});
