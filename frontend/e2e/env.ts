import { readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";

/**
 * End-to-end environment. Playwright runs against a SEPARATE test database (see
 * docker-compose.yml, service postgres-test) that it is free to create, mutate and delete in.
 * Nothing here ever reads or writes the development database.
 */

export const FRONTEND_DIR = path.resolve(__dirname, "..");
export const ROOT_DIR = path.resolve(FRONTEND_DIR, "..");
export const BACKEND_DIR = path.join(ROOT_DIR, "backend");

/**
 * Two end-to-end runs exist, with separate servers (never at the same time, never against the development
 * database): the DEV run (AUTH_MODE=dev, ports 8001/3100) and the SESSION run (AUTH_MODE=session, real login,
 * ports 8002/3101, selected by playwright.session.config.ts through E2E_AUTH).
 */
export const AUTH_E2E: "dev" | "session" = process.env.E2E_AUTH === "session" ? "session" : "dev";
export const BACKEND_PORT = AUTH_E2E === "session" ? 8002 : 8001;
export const FRONTEND_PORT = AUTH_E2E === "session" ? 3101 : 3100;
export const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
export const BASE_URL = `http://127.0.0.1:${FRONTEND_PORT}`;

function dotenv(): Record<string, string | undefined> {
  try {
    return parseEnv(readFileSync(path.join(ROOT_DIR, ".env"), "utf8"));
  } catch {
    return {};
  }
}

export const rootEnv = dotenv();
const merged = { ...rootEnv, ...process.env } as Record<string, string | undefined>;

export const TEST_DATABASE_URL = merged.TEST_DATABASE_URL;
export const DEV_DATABASE_URL = rootEnv.DATABASE_URL;
export const POSTGRES_USER = merged.POSTGRES_USER ?? "business_platform";
export const POSTGRES_DB = merged.POSTGRES_DB ?? "business_platform";
export const POSTGRES_TEST_DB = merged.POSTGRES_TEST_DB ?? "business_platform_test";

/**
 * The browser Playwright drives, as a Playwright `channel` (undefined: Playwright's own Chromium).
 *
 *   E2E_BROWSER=chromium   the Chromium that `npx playwright install chromium` installs, in Playwright's default headless mode
 *                          (the lightweight headless shell). This is what CI uses. It is NOT `channel: "chromium"`, the opt-in
 *                          "new headless" full browser: on hosted 2-vCPU runners that mode stalled on context close and
 *                          mid-test in about one run in eight, the headless shell in none of 40 runs measured.
 *   E2E_BROWSER=msedge|chrome   an installed browser (Edge is the local default)
 *
 * In CI there is NO fallback: a runner has no Microsoft Edge to rely on, so an unset E2E_BROWSER stops the run instead of
 * silently asking for a browser that is not there (the workflows set "chromium", see .github/workflows/ci.yml).
 */
export function browserChannel(): string | undefined {
  const configured = process.env.E2E_BROWSER;
  if (configured === "chromium") return undefined;
  if (configured) return configured;
  if (process.env.CI) throw new Error("E2E_BROWSER is not set. CI has no installed Microsoft Edge: set E2E_BROWSER=chromium (see .github/workflows/ci.yml).");
  return "msedge";
}

/** Traces and screenshots hold cookies, tokens and one-time links; CI keeps none of them (failure artifacts are redacted text only). */
export const TRACE_MODE = process.env.CI ? ("off" as const) : ("retain-on-failure" as const);

export const PYTHON = path.join(BACKEND_DIR, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");

/** Throws unless TEST_DATABASE_URL is set and clearly a test database that is not the dev one. */
export function assertTestDatabase(): string {
  if (!TEST_DATABASE_URL) {
    throw new Error("TEST_DATABASE_URL is not set. End-to-end tests never fall back to the development database (see .env.example).");
  }
  const name = new URL(TEST_DATABASE_URL.replace(/^postgresql\+\w+/, "postgresql")).pathname.replace(/^\//, "");
  if (!name.endsWith("_test")) throw new Error(`Refusing to run: database ${JSON.stringify(name)} does not end in "_test".`);
  if (DEV_DATABASE_URL && DEV_DATABASE_URL === TEST_DATABASE_URL) throw new Error("Refusing to run: TEST_DATABASE_URL equals DATABASE_URL.");
  if (DEV_DATABASE_URL) {
    const server = (value: string) => {
      const parsed = new URL(value.replace(/^postgresql\+\w+/, "postgresql"));
      return `${parsed.hostname}:${parsed.port || "5432"}`;
    };
    if (server(DEV_DATABASE_URL) === server(TEST_DATABASE_URL)) {
      throw new Error("Refusing to run: the test database must live on a different server than the development database.");
    }
  }
  return TEST_DATABASE_URL;
}
