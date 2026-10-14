/**
 * Read a request body with a hard limit on real bytes and on time.
 *
 * `request.text()` buffers everything before any size check can run, a missing `Content-Length` (chunked upload) defeats
 * a header check, and the length of a JavaScript string counts UTF-16 code units, not the bytes that arrived. This reads
 * the stream chunk by chunk, counts bytes, and stops (cancelling the rest) as soon as the limit or the deadline is passed.
 */

export type BodyResult =
  | { ok: true; text: string }
  | { ok: false; reason: "too_large" | "too_slow" | "unreadable" };

/** The HTTP answer for each refusal (413, 408, 400). */
export const BODY_REFUSAL_STATUS = { too_large: 413, too_slow: 408, unreadable: 400 } as const;

export const BODY_REFUSAL_MESSAGE = {
  too_large: "Request body too large",
  too_slow: "Request body took too long to arrive",
  unreadable: "Request body could not be read",
} as const;

export async function readBodyText(request: Request, maxBytes: number, deadlineMs: number): Promise<BodyResult> {
  if (request.body === null) return { ok: true, text: "" };

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<"expired">((resolve) => {
    timer = setTimeout(() => resolve("expired"), deadlineMs);
  });
  // A cancel that cannot complete must never keep a refused request waiting.
  const stop = () => void reader.cancel().catch(() => undefined);

  try {
    for (;;) {
      const next = await Promise.race([reader.read(), expired]);
      if (next === "expired") {
        stop();
        return { ok: false, reason: "too_slow" };
      }
      if (next.done) break;
      total += next.value.byteLength;
      if (total > maxBytes) {
        stop();
        return { ok: false, reason: "too_large" };
      }
      chunks.push(next.value);
    }
  } catch {
    // The connection broke or the stream errored part-way: what arrived is not a complete body.
    return { ok: false, reason: "unreadable" };
  } finally {
    clearTimeout(timer);
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return { ok: true, text: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
  } catch {
    return { ok: false, reason: "unreadable" };
  }
}
