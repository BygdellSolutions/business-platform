// @vitest-environment node
import { describe, expect, it } from "vitest";

import { buildBackendHeaders } from "@/lib/backend";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const TOKEN = "S".repeat(43);

describe("buildBackendHeaders for each kind of credential", () => {
  it("session: Authorization: Bearer <token> and the organization from the URL, nothing that names a user", () => {
    expect(Object.fromEntries(buildBackendHeaders({ credential: { kind: "session", token: TOKEN }, orgId: ORG }))).toEqual({
      accept: "application/json",
      authorization: `Bearer ${TOKEN}`,
      "x-organization-id": ORG,
    });
  });

  it("dev: the dev-user selector header only, never an Authorization header", () => {
    expect(Object.fromEntries(buildBackendHeaders({ credential: { kind: "dev", email: "maria@dev.test" }, orgId: ORG }))).toEqual({
      accept: "application/json",
      "x-dev-user-email": "maria@dev.test",
      "x-organization-id": ORG,
    });
  });

  it("before a session exists there is no credential header at all", () => {
    expect(Object.fromEntries(buildBackendHeaders({ credential: null }, { json: true }))).toEqual({ accept: "application/json", "content-type": "application/json" });
  });

  it("a session credential never produces the dev header, and a dev credential never an Authorization header", () => {
    expect(buildBackendHeaders({ credential: { kind: "session", token: TOKEN } }).has("x-dev-user-email")).toBe(false);
    expect(buildBackendHeaders({ credential: { kind: "dev", email: "a@b.test" } }).has("authorization")).toBe(false);
  });

  it("adds the validated extras only when given: CSRF token, If-Match, client address", () => {
    const bare = buildBackendHeaders({ credential: { kind: "session", token: TOKEN } });
    for (const name of ["x-csrf-token", "if-match", "x-client-ip"]) expect(bare.has(name)).toBe(false);

    const full = buildBackendHeaders({ credential: { kind: "session", token: TOKEN } }, { csrf: "C".repeat(43), ifMatch: '"3"', clientAddress: "198.51.100.7" });
    expect(full.get("x-csrf-token")).toBe("C".repeat(43));
    expect(full.get("if-match")).toBe('"3"');
    expect(full.get("x-client-ip")).toBe("198.51.100.7");
  });

  it("copies nothing from anywhere else: the result has exactly the documented headers", () => {
    const names = [...buildBackendHeaders({ credential: { kind: "session", token: TOKEN }, orgId: ORG }, { json: true, ifMatch: '"1"', csrf: "C".repeat(43), clientAddress: "198.51.100.7", accept: "application/pdf" }).keys()].sort();
    expect(names).toEqual(["accept", "authorization", "content-type", "if-match", "x-client-ip", "x-csrf-token", "x-organization-id"]);
  });
});
