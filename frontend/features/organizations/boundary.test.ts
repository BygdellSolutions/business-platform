// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Static checks that keep organization onboarding inside its design: the URL stays the only tenant selector,
 * nothing about the new organization is remembered by the browser, the request has no owner/role/organization,
 * and the organization-scoped BFF door cannot be used to reach creation.
 */
const ROOT = path.resolve(__dirname, "..", "..");
const read = (file: string) => readFileSync(path.join(ROOT, file), "utf8");
const code = (file: string) => read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("organization onboarding boundaries", () => {
  it("the form and the page never write a cookie or any storage", () => {
    for (const file of ["features/organizations/CreateOrganizationForm.tsx", "features/organizations/request-key.ts", "app/organizations/new/page.tsx"]) {
      expect(code(file), file).not.toMatch(/document\.cookie|localStorage|sessionStorage|indexedDB|cookies\(\)/);
    }
  });

  it("the request body type has no owner, user, role or organization id", () => {
    const type = /export interface OrganizationCreate \{([^}]*)\}/.exec(read("lib/api/types.ts"));
    expect(type).not.toBeNull();
    expect([...type![1].matchAll(/^\s*(\w+)\??:/gm)].map((m) => m[1])).toEqual(["name", "default_currency"]);
  });

  it("the creation request carries no organization, owner or role, in the client or the BFF door", () => {
    for (const file of ["app/api/organizations/route.ts", "features/organizations/CreateOrganizationForm.tsx"]) {
      expect(code(file), file).not.toMatch(/x-organization-id|orgId|owner|x-role|x-dev-user-email/i);
    }
  });

  it("the organization-scoped catch-all cannot reach creation (the plural area is not allowed)", () => {
    const areas = /ALLOWED_API_AREAS = \[([^\]]*)\]/.exec(read("lib/backend.ts"))![1];
    expect(areas).not.toMatch(/"organizations"/);
  });

  it("the form navigates with a full page load to the id the backend returned, and uses no client router", () => {
    const source = code("features/organizations/CreateOrganizationForm.tsx");
    expect(source).toMatch(/window\.location\.assign\(`\/o\/\$\{encodeURIComponent\(created\.id\)\}`\)/);
    expect(source).not.toMatch(/next\/navigation|router\./);
  });

  it("the page decides nothing: the form is shown from the user's own flag and the backend still refuses", () => {
    expect(code("app/organizations/new/page.tsx")).toMatch(/current\.user\.can_create_organizations/);
  });
});
