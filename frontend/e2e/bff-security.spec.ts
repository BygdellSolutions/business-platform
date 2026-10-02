import { expect, test } from "@playwright/test";

import { FREDRIK, MARIA, ORG_A, ORG_B, RANDOM_ORG, bffUrl, createCustomer, signIn } from "./support";

/**
 * Direct requests to the BFF, without the app's pages. This is where "FastAPI is the
 * authority" is proven: the BFF forwards a well-formed organization id, and FastAPI itself
 * refuses an organization the signed-in user does not belong to.
 */

test.describe("FastAPI independently refuses organizations the user does not belong to", () => {
  test("a foreign organization fails exactly like a nonexistent one, with FastAPI's own answer", async ({ context }) => {
    await signIn(context, MARIA); // employee of B only
    const foreign = await context.request.get(bffUrl(ORG_A.id, "/customers"));
    const nonexistent = await context.request.get(bffUrl(RANDOM_ORG, "/customers"));

    expect(foreign.status()).toBe(404);
    expect(await foreign.json()).toEqual({ detail: "Organization not found" }); // FastAPI's text, not the BFF's
    expect(nonexistent.status()).toBe(foreign.status());
    expect(await nonexistent.json()).toEqual(await foreign.json());
  });

  test("the same holds for the other user and organization", async ({ context }) => {
    await signIn(context, FREDRIK);

    const unknown = await context.request.get(bffUrl(RANDOM_ORG, "/customers"));
    expect(unknown.status()).toBe(404);
    expect(await unknown.json()).toEqual({ detail: "Organization not found" });
  });

  test("every method is refused for a foreign organization, and nothing is written", async ({ context }) => {
    await signIn(context, MARIA);
    const probe = "Created By The Wrong Tenant";

    const created = await context.request.post(bffUrl(ORG_A.id, "/customers"), { data: { customer_type: "person", name: probe } });
    const listed = await context.request.get(bffUrl(ORG_A.id, "/customers"));
    const transactions = await context.request.get(bffUrl(ORG_A.id, "/transactions"));
    const fields = await context.request.get(bffUrl(ORG_A.id, "/custom-fields/definitions"));
    const patched = await context.request.patch(bffUrl(ORG_A.id, `/customers/${RANDOM_ORG}`), { data: { name: "x" } });
    const deleted = await context.request.delete(bffUrl(ORG_A.id, `/customers/${RANDOM_ORG}`));

    for (const response of [created, listed, transactions, fields, patched, deleted]) expect(response.status()).toBe(404);

    await context.clearCookies();
    await signIn(context, FREDRIK);
    const names = ((await (await context.request.get(bffUrl(ORG_A.id, "/customers?limit=100"))).json()) as { name: string }[]).map((c) => c.name);
    expect(names).not.toContain(probe);
  });
});

test.describe("client-supplied identity and organization headers cannot override the BFF", () => {
  test("a forged identity and organization are ignored: the cookie and the URL decide", async ({ context }) => {
    await signIn(context, FREDRIK);
    await createCustomer(context, ORG_A.id, "Secret Of Org A");
    await context.clearCookies();
    await signIn(context, MARIA);

    const forged = await context.request.get(bffUrl(ORG_B.id, "/customers?limit=100"), {
      headers: { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id },
    });

    expect(forged.status()).toBe(200);
    const names = ((await forged.json()) as { name: string }[]).map((c) => c.name);
    expect(names).not.toContain("Secret Of Org A"); // still Maria, still organization B
    const me = await context.request.get(bffUrl(ORG_B.id, "/me"), { headers: { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id } });
    expect(await me.json()).toMatchObject({ user: { email: MARIA }, organization: { id: ORG_B.id }, role: "employee" });
  });

  test("a forged organization header cannot reach a foreign organization either", async ({ context }) => {
    await signIn(context, MARIA);

    const forged = await context.request.get(bffUrl(ORG_A.id, "/customers"), { headers: { "x-organization-id": ORG_B.id } });

    expect(forged.status()).toBe(404); // the URL organization (A) was used, and Maria is not in A
  });

  test("forged headers without any cookie are not an identity", async ({ context }) => {
    const response = await context.request.get(bffUrl(ORG_A.id, "/customers"), {
      headers: { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id, authorization: "Bearer x" },
    });

    expect(response.status()).toBe(401);
    expect(await response.json()).toEqual({ detail: "Not authenticated" });
  });

  test("a query parameter cannot choose the organization", async ({ context }) => {
    await signIn(context, MARIA);

    const response = await context.request.get(bffUrl(ORG_A.id, `/customers?organization_id=${ORG_B.id}`));

    expect(response.status()).toBe(404);
  });
});

test.describe("the BFF only forwards what it should", () => {
  test.beforeEach(async ({ context }) => {
    await signIn(context, FREDRIK);
  });

  test("paths outside the API areas, and traversal attempts, never reach FastAPI", async ({ context }) => {
    for (const path of ["/health", "/docs", "/openapi.json", "/customers/..%2F..%2Fhealth", "/customers/%2e%2e/health", "/customers/..\\health", "/me/../../health"]) {
      const response = await context.request.get(bffUrl(ORG_A.id, path));
      expect(response.status(), path).toBe(404);
      expect(await response.text(), path).not.toContain('"status":"ok"'); // FastAPI's /health answer
    }
  });

  test("a malformed organization id is rejected before the backend", async ({ context }) => {
    for (const org of ["not-a-uuid", "..", "%2e%2e"]) {
      expect((await context.request.get(`/api/o/${org}/customers`)).status(), org).toBe(404);
    }
  });

  test("bodies must be JSON", async ({ context }) => {
    const response = await context.request.post(bffUrl(ORG_A.id, "/customers"), {
      headers: { "content-type": "text/plain" },
      data: "name=x",
    });

    expect(response.status()).toBe(415);
  });

  test("cross-site writes are refused", async ({ context }) => {
    const response = await context.request.post(bffUrl(ORG_A.id, "/customers"), {
      headers: { origin: "http://evil.test", "content-type": "application/json" },
      data: { customer_type: "person", name: "From another site" },
    });

    expect(response.status()).toBe(403);
  });

  test("unsupported methods do not exist", async ({ context }) => {
    expect((await context.request.put(bffUrl(ORG_A.id, "/customers"), { data: {} })).status()).toBe(405);
  });

  test("backend answers pass through unchanged, including validation errors", async ({ context }) => {
    const response = await context.request.post(bffUrl(ORG_A.id, "/customers"), { data: { customer_type: "person", name: "", organization_id: ORG_B.id } });

    expect(response.status()).toBe(422);
    const body = (await response.json()) as { detail: { loc: string[]; type: string }[] };
    expect(body.detail.map((d) => d.loc.join("."))).toEqual(expect.arrayContaining(["body.name", "body.organization_id"]));
    expect(response.headers()["set-cookie"]).toBeUndefined();
  });
});
