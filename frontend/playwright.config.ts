import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run against the production-style build: FastAPI serves the built frontend
// on :8031 with its own database (decision_hub_e2e), reset and seeded on every run.
const DB = process.env.DH_E2E_DATABASE_URL ?? "postgresql+psycopg:///decision_hub_e2e";
const PORT = 8031;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // one shared database
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    channel: "chromium", // full Chromium in new headless mode (no separate headless-shell download)
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testMatch: /workflow\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /responsive\.spec\.ts/ },
  ],
  webServer: {
    command:
      "npm run build && cd ../backend && " +
      "uv run alembic downgrade base && uv run alembic upgrade head && uv run python -m app.seed && " +
      `uv run uvicorn app.main:app --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/v1/health`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: {
      DH_DATABASE_URL: DB,
      DH_STATIC_DIR: "../frontend/dist",
      DH_LLM_PROVIDER: "mock",
      DH_SESSION_SECRET: "e2e-only-not-a-real-secret",
    },
  },
});
