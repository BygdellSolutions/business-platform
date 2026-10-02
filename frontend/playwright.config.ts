import { defineConfig } from "@playwright/test";

import { BACKEND_DIR, BACKEND_PORT, BACKEND_URL, BASE_URL, FRONTEND_DIR, FRONTEND_PORT, PYTHON, assertTestDatabase } from "./e2e/env";

/**
 * End-to-end tests run against a REAL stack that is entirely separate from development:
 *   - a backend on :8001 connected ONLY to the test database (postgres-test, :5433),
 *   - a production build of the frontend on :3100 talking to that backend,
 *   - a global setup that rebuilds and seeds the test database before every run.
 * `reuseExistingServer` is off on purpose: a leftover dev server must never be picked up.
 *
 * Prerequisite: `docker compose up -d postgres-test`.
 * Browser: the installed Microsoft Edge by default (set E2E_BROWSER=chrome for Chrome).
 */
const testDatabaseUrl = assertTestDatabase();

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1, // the specs share one seeded database
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    channel: process.env.E2E_BROWSER ?? "msedge",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `"${PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: BACKEND_DIR,
      url: `${BACKEND_URL}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...(process.env as Record<string, string>),
        DATABASE_URL: testDatabaseUrl,
        APP_ENV: "development",
        AUTH_MODE: "dev",
        DEV_USER_EMAIL: "", // no ambient identity: only the BFF cookie identifies anyone
        CORS_ORIGINS: "[]",
      },
    },
    {
      command: `npm run build && npm run start -- --hostname 127.0.0.1 --port ${FRONTEND_PORT}`,
      cwd: FRONTEND_DIR,
      url: `${BASE_URL}/dev-login`,
      reuseExistingServer: false,
      timeout: 240_000,
      env: {
        ...(process.env as Record<string, string>),
        BACKEND_URL,
        DEV_IDENTITY: "enabled",
      },
    },
  ],
});
