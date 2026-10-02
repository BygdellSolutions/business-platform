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

export const BACKEND_PORT = 8001;
export const FRONTEND_PORT = 3100;
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
