import { execFileSync } from "node:child_process";
import { expect, type BrowserContext, type Page } from "@playwright/test";

import { BACKEND_DIR, BACKEND_URL, BASE_URL, PYTHON, assertTestDatabase } from "./env";

/**
 * Real authentication for the SESSION end-to-end run.
 *
 * Credentials are provisioned the way production does it, in the disposable TEST database only: the operator
 * CLI issues a single-use setup link for an existing user and the user redeems it (here straight at FastAPI,
 * for speed; the browser path is covered by its own specs). `seed_dev` creates no credential and is never a
 * credential mechanism.
 */

// Throwaway test values for a DISPOSABLE database. CI generates fresh ones per job (E2E_PASSWORD, E2E_BFF_SECRET,
// E2E_SECURITY_KEY, masked in the log); the defaults only serve a developer's machine.
export const E2E_PASSWORD = process.env.E2E_PASSWORD ?? "e2e long passphrase 123";

/**
 * The SESSION run enforces the BFF internal secret end to end: the BFF sends it, FastAPI requires it. A test that talks
 * to FastAPI directly (to check what the backend itself says) must act as the BFF, so it goes through `direct`.
 * Specs that prove what happens WITHOUT the secret use a bare `fetch`.
 */
export const E2E_BFF_SECRET = process.env.E2E_BFF_SECRET ?? "e2e-bff-secret-5c1f9a3e7b2d40869e1c7a35b8d20f64";

export function direct(url: string, init: RequestInit = {}): Promise<Response> {
  return fetch(url, { ...init, headers: { ...(init.headers as Record<string, string> | undefined), "x-bff-secret": E2E_BFF_SECRET } });
}

/** Cheap Argon2 for tests: the algorithm is the real one, only the cost is small. */
export const BACKEND_AUTH_ENV: Record<string, string> = {
  ARGON2_MEMORY_KIB: "1024",
  ARGON2_TIME_COST: "1",
  ARGON2_PARALLELISM: "1",
  SECURITY_KEY: process.env.E2E_SECURITY_KEY ?? "e2e-session-security-key-0123456789abcdef",
  BFF_INTERNAL_SECRET: E2E_BFF_SECRET,
  // Many specs sign in from the one local address: keep their throttling budgets out of each other's way.
  THROTTLE_SOURCE_MAX_FAILURES: "1000",
  THROTTLE_PAIR_MAX_FAILURES: "100",
  THROTTLE_IDENTIFIER_MAX_FAILURES: "1000",
};

export function adminCli(args: string[]): string {
  return execFileSync(PYTHON, ["-m", "app.scripts.admin", ...args], {
    cwd: BACKEND_DIR,
    encoding: "utf8",
    env: { ...process.env, DATABASE_URL: assertTestDatabase(), APP_ENV: "development", AUTH_MODE: "session", PUBLIC_ORIGIN: BASE_URL, ...BACKEND_AUTH_ENV },
  });
}

/** The single-use link token printed by the CLI. */
export function tokenFromCli(output: string): string {
  const found = /#([A-Za-z0-9_-]{43})/.exec(output);
  if (!found) throw new Error(`no setup link in the CLI output: ${output}`);
  return found[1];
}

const provisioned = new Set<string>();

/** Give an EXISTING user the e2e password (once per run and user). */
export async function ensureCredential(email: string, password: string = E2E_PASSWORD): Promise<void> {
  const key = `${email}|${password}`;
  if (provisioned.has(key)) return;
  const token = tokenFromCli(adminCli(["reissue-setup-link", "--email", email]));
  const response = await direct(`${BACKEND_URL}/api/auth/setup`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token, password }) });
  if (!response.ok) throw new Error(`could not provision ${email}: ${response.status} ${await response.text()}`);
  provisioned.add(key);
}

/** The pre-auth secret the login and setup pages use (cookie in the context, value returned). */
export async function preAuthToken(context: BrowserContext): Promise<string> {
  const response = await context.request.get("/api/auth/pre");
  expect(response.status()).toBe(200);
  return ((await response.json()) as { token: string }).token;
}

/** The cookie jar's CSRF token (the readable cookie), if signed in. */
export async function csrfCookie(context: BrowserContext): Promise<string | undefined> {
  return (await context.cookies()).find((cookie) => cookie.name === "bp_csrf")?.value;
}

/**
 * Make every state-changing `context.request` call carry what the browser's own scripts would: the canonical
 * Origin and the CSRF header taken from the CSRF cookie. Specs written for the dev run then work unchanged.
 * A caller's own headers win, so a spec can still forge or omit them on purpose.
 */
export function installBrowserHeaders(context: BrowserContext): void {
  const marker = Symbol.for("bp.browser-headers");
  const holder = context as unknown as Record<symbol, boolean>;
  if (holder[marker]) return;
  holder[marker] = true;
  const original = context.request;
  const wrap = (method: "post" | "patch" | "put" | "delete") => async (url: string, options: { headers?: Record<string, string> } & Record<string, unknown> = {}) => {
    const csrf = await csrfCookie(context);
    const headers = { origin: BASE_URL, ...(csrf ? { "x-csrf-token": csrf } : {}), ...(options.headers ?? {}) };
    return (original[method] as (u: string, o: unknown) => Promise<unknown>).call(original, url, { ...options, headers });
  };
  const proxy = new Proxy(original, {
    get(target, property) {
      if (property === "post" || property === "patch" || property === "put" || property === "delete") return wrap(property);
      const value = Reflect.get(target, property);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
  Object.defineProperty(context, "request", { value: proxy, configurable: true });
}

/** Sign in through the REAL login route handler; the protected cookies land in the browser context. */
export async function signInWithPassword(context: BrowserContext, email: string, password: string = E2E_PASSWORD): Promise<void> {
  await ensureCredential(email, password);
  const secret = await preAuthToken(context);
  const response = await context.request.post("/api/auth/login", { headers: { origin: BASE_URL, "x-pre-auth": secret }, data: { email, password } });
  expect(response.status(), await response.text()).toBe(200);
  installBrowserHeaders(context);
}

/** Sign in through the login PAGE, as a person would. */
export async function signInThroughPage(page: Page, email: string, password: string = E2E_PASSWORD, next?: string): Promise<void> {
  await ensureCredential(email, password);
  await page.goto(next === undefined ? "/login" : `/login?next=${encodeURIComponent(next)}`);
  await expect(page.getByTestId("login-form")).toHaveAttribute("data-ready", "true");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByTestId("login-submit").click();
  await page.waitForURL((url) => url.pathname !== "/login"); // the login finished: cookies are set and the page has moved on
  installBrowserHeaders(page.context());
}
