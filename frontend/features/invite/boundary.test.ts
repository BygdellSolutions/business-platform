// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Static checks that keep the invitation secret inside its design: it lives in the URL fragment, is read and
 * removed in the browser, travels only in POST bodies, and is never stored, redirected, logged or placed in a URL.
 */
const ROOT = path.resolve(__dirname, "..", "..");
const read = (file: string) => readFileSync(path.join(ROOT, file), "utf8");
const code = (file: string) => read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const INVITE_FILES = ["features/invite/InviteFlow.tsx", "app/invite/page.tsx", "app/api/invite/preview/route.ts", "app/api/invite/accept/route.ts", "app/api/invite/accept-new/route.ts", "features/members/InvitationsAdmin.tsx", "lib/invite.ts"];

describe("invitation secret handling", () => {
  it("nothing in the invitation code writes a cookie or uses any storage", () => {
    for (const file of INVITE_FILES) expect(code(file), file).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie\s*=|cookies\(\)\.set|\.cookies\.set/);
  });

  it("the invite component removes the fragment from the address and the history, and reads no query string", () => {
    const source = code("features/invite/InviteFlow.tsx");
    expect(source).toMatch(/window\.history\.replaceState\(null, "", window\.location\.pathname \+ window\.location\.search\)/);
    expect(source).toMatch(/window\.location\.hash/);
    expect(source).not.toMatch(/useSearchParams|searchParams|location\.search\.|URLSearchParams/);
  });

  it("the server-rendered page never sees the fragment: it reads no hash, query, header or the token", () => {
    const source = code("app/invite/page.tsx");
    expect(source).not.toMatch(/searchParams|headers\(\)|hash|token/i);
  });

  it("no invitation code puts the token in a redirect, a login address, a router call or a URL", () => {
    for (const file of INVITE_FILES.filter((f) => f !== "lib/invite.ts")) {
      const source = code(file);
      expect(source, file).not.toMatch(/redirect\(|router\.push|router\.replace|\/login\?next|next=|\?token=|&token=|\/invite\/\$\{/);
    }
    // the only place a link is built puts the secret after the "#"
    expect(code("lib/invite.ts")).toMatch(/`\$\{origin\}\/invite#\$\{token\}`/);
  });

  it("the BFF doors never receive or forward an organization, owner or role, and are POST only", () => {
    for (const file of ["app/api/invite/preview/route.ts", "app/api/invite/accept/route.ts", "app/api/invite/accept-new/route.ts"]) {
      const source = code(file);
      expect(source, file).not.toMatch(/x-organization-id|orgId|owner|x-role|x-dev-user-email/i);
      expect(source, file).toMatch(/export const POST = instrument\(/);
      expect(source, file).not.toMatch(/export (async )?function (GET|PUT|PATCH|DELETE)|export const (GET|PUT|PATCH|DELETE)/);
    }
  });

  it("every invitation door is session-mode only", () => {
    for (const file of ["app/api/invite/preview/route.ts", "app/api/invite/accept/route.ts", "app/api/invite/accept-new/route.ts"]) expect(code(file), file).toMatch(/sessionModeOnly\(\)/);
  });

  it("the invite page and its API are served uncached and without a referrer", () => {
    const config = code("next.config.ts");
    expect(config).toMatch(/source: "\/invite", headers: \[NO_STORE, NO_REFERRER\]/);
    expect(config).toMatch(/source: "\/api\/invite\/:path\*", headers: \[NO_STORE, NO_REFERRER\]/);
  });

  it("the pre-auth doors apply the pre-auth double submit and the signed-in door the session CSRF, each BEFORE contacting FastAPI", () => {
    for (const file of ["app/api/invite/preview/route.ts", "app/api/invite/accept-new/route.ts"]) {
      expect(code(file), file).toMatch(/preAuthProblem\(request\)/);
      expect(code(file).indexOf("preAuthProblem"), file).toBeLessThan(code(file).indexOf("backendFetch") === -1 ? Infinity : code(file).indexOf("backendFetch"));
    }
    const accept = code("app/api/invite/accept/route.ts");
    expect(accept).toMatch(/validCsrf\(request\)/);
    expect(accept).toMatch(/originProblem\(request\)/);
    expect(accept.indexOf("validCsrf")).toBeLessThan(accept.indexOf("backendFetch"));
  });

  it("the administration link is built only by inviteLink, from the browser's own origin", () => {
    const source = code("features/members/InvitationsAdmin.tsx");
    expect(source).toMatch(/inviteLink\(window\.location\.origin, created\.token\)/);
    expect(source).not.toMatch(/\/invite#/);
  });
});
