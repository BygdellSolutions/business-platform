// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { readBodyText } from "@/lib/request-body";

/**
 * The bounded body reader. Every case builds a streamed request WITHOUT a Content-Length, because that is the
 * request a header check cannot see.
 */

const encoder = new TextEncoder();

type Probe = { pulls: number; cancelled: boolean };

/** A request whose body arrives in the given chunks; `after` decides what happens once they are used up. */
function streamed(chunks: Uint8Array[], after: "end" | "hang" | "error" = "end"): { request: Request; probe: Probe } {
  const probe: Probe = { pulls: 0, cancelled: false };
  let index = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      probe.pulls += 1;
      if (index < chunks.length) {
        controller.enqueue(chunks[index++]);
        return;
      }
      if (after === "end") controller.close();
      else if (after === "error") controller.error(new Error("connection reset"));
      else return new Promise<void>(() => undefined); // never delivers again
    },
    cancel() {
      probe.cancelled = true;
    },
  });
  const request = new Request("http://localhost/x", { method: "POST", body, duplex: "half" } as RequestInit);
  expect(request.headers.get("content-length")).toBeNull();
  return { request, probe };
}

const bytes = (text: string) => encoder.encode(text);

afterEach(() => {
  vi.useRealTimers();
});

describe("readBodyText", () => {
  it("returns the text of a body within the limit", async () => {
    const { request } = streamed([bytes('{"a":'), bytes('"b"}')]);

    expect(await readBodyText(request, 100, 1000)).toEqual({ ok: true, text: '{"a":"b"}' });
  });

  it("treats a request without a body as empty", async () => {
    const request = new Request("http://localhost/x", { method: "POST" });

    expect(await readBodyText(request, 100, 1000)).toEqual({ ok: true, text: "" });
  });

  it("accepts exactly the limit and refuses one byte more", async () => {
    expect(await readBodyText(streamed([bytes("x".repeat(10))]).request, 10, 1000)).toEqual({ ok: true, text: "x".repeat(10) });
    expect(await readBodyText(streamed([bytes("x".repeat(11))]).request, 10, 1000)).toEqual({ ok: false, reason: "too_large" });
  });

  it("counts bytes, not characters: multibyte text over the limit is refused", async () => {
    const text = "€".repeat(6_000); // 6,000 characters but 18,000 bytes
    expect(text.length).toBeLessThan(16_384);

    expect(await readBodyText(streamed([bytes(text)]).request, 16_384, 1000)).toEqual({ ok: false, reason: "too_large" });
  });

  it("counts bytes across chunks, with no Content-Length to help", async () => {
    const chunks = Array.from({ length: 50 }, () => bytes("x".repeat(100)));

    expect(await readBodyText(streamed(chunks).request, 1_000, 1000)).toEqual({ ok: false, reason: "too_large" });
  });

  it("stops reading and cancels the stream once the limit is passed", async () => {
    const chunks = Array.from({ length: 1_000 }, () => bytes("x".repeat(100)));
    const { request, probe } = streamed(chunks);

    expect(await readBodyText(request, 250, 1000)).toEqual({ ok: false, reason: "too_large" });
    expect(probe.cancelled).toBe(true);
    // 3 chunks pass the limit; the stream may be pulled ahead by a few, never through all 1,000.
    expect(probe.pulls).toBeLessThan(20);
  });

  it("refuses a body that stops arriving, and cancels it", async () => {
    vi.useFakeTimers();
    const { request, probe } = streamed([bytes("abc")], "hang");

    const pending = readBodyText(request, 100, 5_000);
    await vi.advanceTimersByTimeAsync(5_000);

    expect(await pending).toEqual({ ok: false, reason: "too_slow" });
    expect(probe.cancelled).toBe(true);
  });

  it("does not hold the deadline timer after a normal read", async () => {
    vi.useFakeTimers();
    await readBodyText(streamed([bytes("abc")]).request, 100, 5_000);

    expect(vi.getTimerCount()).toBe(0);
  });

  it("calls a broken connection unreadable", async () => {
    expect(await readBodyText(streamed([bytes("abc")], "error").request, 100, 1000)).toEqual({ ok: false, reason: "unreadable" });
  });

  it("refuses bytes that are not valid UTF-8", async () => {
    const { request } = streamed([new Uint8Array([0x7b, 0xff, 0xfe, 0x7d])]);

    expect(await readBodyText(request, 100, 1000)).toEqual({ ok: false, reason: "unreadable" });
  });

  it("keeps a multibyte character that is split across two chunks", async () => {
    const euro = bytes("€"); // three bytes
    const { request } = streamed([euro.slice(0, 1), euro.slice(1)]);

    expect(await readBodyText(request, 100, 1000)).toEqual({ ok: true, text: "€" });
  });
});
