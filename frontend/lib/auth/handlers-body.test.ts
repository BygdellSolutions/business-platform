// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { readJsonObject } from "@/lib/auth/handlers";

/** The body of the login, setup, invite and change-password endpoints: JSON, at most 16,384 BYTES. */

function post(text: string, { chunked = true }: { chunked?: boolean } = {}) {
  const data = new TextEncoder().encode(text);
  const headers = { "content-type": "application/json" };
  const init = chunked
    ? {
        method: "POST",
        headers,
        duplex: "half",
        body: new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(data);
            controller.close();
          },
        }),
      }
    : { method: "POST", headers, body: text };
  return new NextRequest("http://localhost/api/auth/login", init as ConstructorParameters<typeof NextRequest>[1]);
}

describe("readJsonObject", () => {
  it("returns a small JSON object, streamed or not", async () => {
    expect(await readJsonObject(post('{"email":"a@b.test"}'))).toEqual({ email: "a@b.test" });
    expect(await readJsonObject(post('{"email":"a@b.test"}', { chunked: false }))).toEqual({ email: "a@b.test" });
  });

  it("refuses a streamed body over the limit (no Content-Length to check)", async () => {
    expect(await readJsonObject(post(JSON.stringify({ x: "y".repeat(100_000) })))).toBeNull();
  });

  it("refuses multibyte JSON that is under the limit in characters but over it in bytes", async () => {
    const text = JSON.stringify({ x: "€".repeat(6_000) }); // 6,011 characters, 18,011 bytes
    expect(text.length).toBeLessThan(16_384);

    expect(await readJsonObject(post(text))).toBeNull();
  });

  it("refuses JSON that is not an object", async () => {
    expect(await readJsonObject(post("[1,2]"))).toBeNull();
    expect(await readJsonObject(post("not json"))).toBeNull();
  });
});
