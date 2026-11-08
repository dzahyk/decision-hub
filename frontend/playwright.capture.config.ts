import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Screenshot capture for the landing page and the portfolio case study (real UI, synthetic data).
// Run: npx playwright test -c playwright.capture.config.ts
export default defineConfig({
  ...base,
  testDir: "./e2e-capture",
  projects: [{ name: "capture", use: { channel: "chromium" } }],
  reporter: [["list"]],
});
