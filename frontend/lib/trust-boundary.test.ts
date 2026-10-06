// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Static checks that keep the BFF's trust boundary mechanical: ONE module talks to FastAPI, the trust headers are named
 * in one place each, the internal secret is read in one place, every API route is instrumented (request id and the one
 * safe log line), and only the observability module writes logs.
 */
const ROOT = path.resolve(__dirname, "..");

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

const rel = (file: string) => path.relative(ROOT, file).replaceAll("\\", "/");
const strip = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const ALL = [...["app", "components", "features", "lib"].flatMap((dir) => sources(path.join(ROOT, dir))), path.join(ROOT, "proxy.ts"), path.join(ROOT, "instrumentation.ts"), path.join(ROOT, "instrumentation-node.ts"), path.join(ROOT, "next.config.ts")];
const code = (file: string) => strip(readFileSync(file, "utf8"));
const isClient = (file: string) => /^\s*["']use client["']/.test(readFileSync(file, "utf8"));
const matching = (pattern: RegExp, candidates = ALL) => candidates.filter((file) => pattern.test(code(file))).map(rel).sort();

describe("one module talks to FastAPI", () => {
  it("only lib/backend.ts fetches from server code (the browser's own fetches are in client code and lib/api/client.ts)", () => {
    const serverSide = ALL.filter((file) => !isClient(file) && rel(file) !== "lib/api/client.ts");
    expect(matching(/\bfetch\(/, serverSide)).toEqual(["lib/backend.ts"]);
  });

  it("the backend address is read only by lib/runtime-config.ts", () => {
    expect(matching(/process\.env\.BACKEND_URL|BACKEND_URL/)).toEqual(["lib/runtime-config.ts"]);
  });
});

describe("the trust headers are named in one place each", () => {
  it("the internal secret header constant is used only by the module that builds upstream headers", () => {
    expect(matching(/BFF_SECRET_HEADER/)).toEqual(["lib/backend.ts", "lib/runtime-config.ts"]);
    expect(matching(/["'`]x-bff-secret["'`]/i)).toEqual(["lib/runtime-config.ts"]);
  });

  it("the secret value is read only by lib/runtime-config.ts and used only by lib/backend.ts", () => {
    expect(matching(/BFF_INTERNAL_SECRET/)).toEqual(["lib/runtime-config.ts"]);
    expect(matching(/\bbffSecret\(/)).toEqual(["lib/backend.ts", "lib/runtime-config.ts"]);
  });

  it("the client-address header is built only by lib/backend.ts", () => {
    expect(matching(/["'`]x-client-ip["'`]/i)).toEqual(["lib/backend.ts"]);
  });

  it("forwarding headers are read in exactly one function (the client address derivation)", () => {
    expect(matching(/["'`](x-forwarded-for|x-forwarded-host|x-forwarded-proto|x-real-ip|forwarded)["'`]/i)).toEqual(["lib/auth/request.ts"]);
  });

  it("the request id header is named only by the observability module (everyone else uses its constant)", () => {
    expect(matching(/["'`]x-request-id["'`]/i)).toEqual(["lib/observability.ts"]);
  });

  it("no browser-visible variable, public config or build-time inlining carries any of it", () => {
    expect(matching(/NEXT_PUBLIC_/)).toEqual([]);
    const config = code(path.join(ROOT, "next.config.ts"));
    expect(config).not.toMatch(/\benv\s*:|publicRuntimeConfig|serverRuntimeConfig|BFF_INTERNAL_SECRET|BACKEND_URL/);
  });

  it("no client component imports a module that holds the secret, the logger or the upstream contract", () => {
    for (const file of ALL.filter(isClient)) {
      expect(readFileSync(file, "utf8"), rel(file)).not.toMatch(/@\/lib\/(runtime-config|observability|upstream|backend)"/);
    }
  });
});

describe("every API route is instrumented", () => {
  const routes = ALL.filter((file) => /^app\/api\/.*route\.ts$/.test(rel(file)));

  it("there are routes to check", () => {
    expect(routes.length).toBeGreaterThanOrEqual(12);
  });

  it.each(routes.map((file) => [rel(file), file]))("%s exports only instrumented handlers", (_name, file) => {
    const source = code(file);
    const exported = [...source.matchAll(/^export (?:async )?(?:function|const) (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b/gm)].map((m) => m[1]);
    expect(exported.length).toBeGreaterThan(0);
    for (const method of exported) expect(source, `${method} in ${_name}`).toMatch(new RegExp(`export const ${method} = instrument\\(`));
    expect(source).toContain('from "@/lib/observability"');
  });
});

describe("only the observability module writes logs", () => {
  it("nothing else writes to stdout or stderr (startup refusals use console.error in instrumentation-node.ts only)", () => {
    expect(matching(/process\.(stdout|stderr)/)).toEqual(["lib/observability.ts"]);
    expect(matching(/\bconsole\.(log|info|debug|warn|error)\b/)).toEqual(["instrumentation-node.ts"]);
  });

  it("the startup refusal prints reasons, never configured values", () => {
    const source = code(path.join(ROOT, "instrumentation-node.ts"));
    expect(source).not.toMatch(/process\.env/);
    for (const file of [path.join(ROOT, "lib/runtime-config.ts")]) expect(code(file)).not.toMatch(/\$\{[^}]*process\.env/);
  });
});
