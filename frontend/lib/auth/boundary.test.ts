// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Static trust-boundary checks. They keep three things true mechanically:
 *
 *   1. Client code never reaches server-only authentication code and never names the session cookie.
 *   2. No browser storage is used anywhere for anything (so no token can be stored in it).
 *   3. Upstream authentication is built in ONE place (`lib/backend.ts`), and no authentication source logs.
 */
const ROOT = path.resolve(__dirname, "..", "..");

function sources(directory: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(directory)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = path.join(directory, entry);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry) && !/\.spec\.ts$/.test(entry)) out.push(full);
  }
  return out;
}

const ALL = ["app", "components", "features", "lib"].flatMap((dir) => sources(path.join(ROOT, dir)));
const rel = (file: string) => path.relative(ROOT, file).replaceAll("\\", "/");
const read = (file: string) => readFileSync(file, "utf8");
const isClient = (source: string) => /^\s*["']use client["']/.test(source);
const CLIENT = ALL.filter((file) => isClient(read(file)));

const SERVER_ONLY_AUTH = ["@/lib/auth/credential", "@/lib/auth/request", "@/lib/auth/handlers", "@/lib/auth/session-cookies", "@/lib/auth/server", "@/lib/backend", "@/lib/identity", "@/lib/orgs", "@/lib/server-api", "@/lib/runtime-config", "@/lib/observability", "@/lib/upstream"];

describe("client code and the session", () => {
  it("there is client code to check", () => {
    expect(CLIENT.length).toBeGreaterThan(10);
  });

  it.each(CLIENT.map((file) => [rel(file), file]))("%s imports no server-only authentication or backend module", (_name, file) => {
    const source = read(file);
    for (const specifier of SERVER_ONLY_AUTH) expect(source).not.toContain(`"${specifier}"`);
  });

  it.each(CLIENT.map((file) => [rel(file), file]))("%s never names the session cookie or reads cookies other than the CSRF cookie", (_name, file) => {
    const source = read(file);
    expect(source).not.toMatch(/bp_session|__Host-bp_session|bp_dev_user|bp_pre/);
    if (/document\.cookie/.test(source)) expect(["lib/api/client.ts", "components/shell/SignOut.tsx"]).toContain(rel(file));
  });

  it("the shared cookie constants name the CSRF cookie only (never the session cookie)", () => {
    const source = read(path.join(ROOT, "lib/auth/cookies.ts"));
    expect(source).toContain("bp_csrf");
    expect(source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "")).not.toMatch(/bp_session/);
  });
});

describe("no browser storage", () => {
  it.each(ALL.map((file) => [rel(file), file]))("%s uses no localStorage, sessionStorage, IndexedDB or the Cache API", (_name, file) => {
    const code = read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
    expect(code).not.toMatch(/\b(localStorage|sessionStorage|indexedDB|caches\.open)\b/);
  });
});

describe("upstream authentication is built in one place", () => {
  const AUTH_HEADER = /["'`]authorization["'`]|\bauthorization\s*:/i;

  it("only lib/backend.ts constructs an Authorization header", () => {
    const builders = ALL.filter((file) => AUTH_HEADER.test(read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1"))).map(rel);
    expect(builders).toEqual(["lib/backend.ts"]);
  });

  it("no route handler forwards the incoming request's headers wholesale", () => {
    for (const file of ALL.filter((f) => /app\/api\//.test(rel(f)))) {
      const code = read(file);
      expect(code, rel(file)).not.toMatch(/headers:\s*request\.headers|new Headers\(request\.headers\)|\.\.\.request\.headers/);
    }
  });

  it("authentication code and the BFF never log", () => {
    const covered = ALL.filter((file) => /^(lib\/auth\/|app\/api\/|lib\/backend\.ts|lib\/pdf-response\.ts)/.test(rel(file)));
    expect(covered.length).toBeGreaterThan(8);
    for (const file of covered) expect(read(file), rel(file)).not.toMatch(/\bconsole\.(log|info|debug|warn|error)\b/);
  });

  it("the BFF never echoes a token field of an upstream body to the browser", () => {
    for (const name of ["app/api/auth/login/route.ts", "app/api/auth/setup/route.ts"]) {
      const source = read(path.join(ROOT, name));
      expect(source).toContain("signedIn(");
      expect(source).not.toMatch(/json\([^)]*\b(token|csrf)\b/);
    }
  });

  it("the dev adapter is the only code that knows the dev cookie and header names", () => {
    const users = ALL.filter((file) => /bp_dev_user|x-dev-user-email|DEV_USER_COOKIE/i.test(read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1"))).map(rel).sort();
    expect(users).toEqual(["app/api/dev-session/route.ts", "lib/auth/credential.ts", "lib/backend.ts", "lib/identity.ts"]);
  });
});

describe("the authentication mode is decided in one place", () => {
  it("business code never reads AUTH_MODE, APP_ENV or PUBLIC_ORIGIN", () => {
    const readers = ALL.filter((file) => /process\.env\.(AUTH_MODE|APP_ENV|PUBLIC_ORIGIN|TRUSTED_PROXY_HOPS|DEV_IDENTITY)/.test(read(file))).map(rel);
    expect(readers).toEqual(["lib/auth/config.ts"]);
  });

  it("only the shell, the authentication pages and the BFF routes branch on the mode", () => {
    const branching = ALL.filter((file) => /\bauthMode\(\)|\bauthConfig\(\)|\bdevIdentityEnabled\(\)/.test(read(file)) && rel(file) !== "lib/auth/config.ts").map(rel).sort();
    expect(branching).toEqual([
      "app/api/dev-session/route.ts",
      "app/api/o/[orgId]/[...path]/route.ts",
      "app/api/organizations/route.ts",
      "app/dev-login/page.tsx",
      "app/invite/page.tsx",
      "app/login/page.tsx",
      "app/o/[orgId]/layout.tsx",
      "app/page.tsx",
      "app/setup/page.tsx",
      "lib/auth/credential.ts",
      "lib/auth/handlers.ts",
      "lib/auth/request.ts",
      "lib/runtime-config.ts",
    ]);
  });
});
