import "./e2e/session-env"; // FIRST: selects the session ports and helpers before anything reads them

import { defineConfig } from "@playwright/test";

import { BACKEND_AUTH_ENV } from "./e2e/auth-support";
import { BACKEND_DIR, BACKEND_PORT, BACKEND_URL, BASE_URL, FRONTEND_DIR, FRONTEND_PORT, PYTHON, assertTestDatabase } from "./e2e/env";

/**
 * The SESSION end-to-end run: real authentication, end to end.
 *
 *   browser -> Next.js BFF (:3101, AUTH_MODE=session) -> FastAPI (:8002, AUTH_MODE=session) -> the TEST database
 *
 * It runs next to, never instead of, the dev run (playwright.config.ts): the same test database, rebuilt and
 * seeded by the same global setup, but its own servers. Users get their passwords through the real mechanism
 * (the operator CLI's single-use link) in e2e/auth-support.ts; `seed_dev` creates none.
 *
 * The backend is started with DEV_USER_EMAIL set to a seeded user ON PURPOSE: in session mode it must have no
 * effect, and the specs prove it. CORS is off (FastAPI is reached by the BFF only).
 *
 * Run:  npm run test:e2e:session     (prerequisite: docker compose up -d postgres-test)
 */
const testDatabaseUrl = assertTestDatabase();

export default defineConfig({
  testDir: "./e2e",
  // The session-only specs, plus the representative dev specs that must behave identically under session auth.
  testMatch: [
    "**/session/*.spec.ts",
    "**/tenant-isolation.spec.ts",
    "**/customers-catalog-isolation.spec.ts",
    "**/transactions-isolation.spec.ts",
    "**/invoices-isolation.spec.ts",
    "**/customers.spec.ts",
    "**/catalog.spec.ts",
    "**/horses.spec.ts",
    "**/transactions.spec.ts",
    "**/invoices.spec.ts",
    "**/onboarding.spec.ts",
  ],
  // Left to the dev run because they exercise the DEV identity itself: forged X-Dev-User-Email headers (the session
  // run has its own specs for forged identity, organization and authorization headers) and checks that read FastAPI
  // directly with the dev header. The decimal round trips do not depend on the identity mechanism.
  grepInvert: /forged headers|decimal values survive|ad-hoc lines and exact decimals/,
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
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
        AUTH_MODE: "session",
        DEV_USER_EMAIL: "fredrik@dev.test", // must be ignored in session mode (proved by specs)
        CORS_ORIGINS: "[]",
        ...BACKEND_AUTH_ENV,
      },
    },
    {
      command: `npm run build && npm run start -- --hostname 127.0.0.1 --port ${FRONTEND_PORT}`,
      cwd: FRONTEND_DIR,
      url: `${BASE_URL}/login`,
      reuseExistingServer: false,
      timeout: 240_000,
      env: {
        ...(process.env as Record<string, string>),
        BACKEND_URL,
        AUTH_MODE: "session",
        APP_ENV: "development",
        PUBLIC_ORIGIN: BASE_URL,
        DEV_IDENTITY: "enabled", // the retired switch: must change nothing
      },
    },
  ],
});
