import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

// The BFF writes one JSON log line per request and per upstream failure (lib/observability.ts). Tests that exercise failures
// would flood the console (and the CI log) with them; drop those lines unless a test installs its own spy on stdout/stderr
// (a spy replaces this wrapper, so the tests that assert on logging still see every line).
for (const stream of [process.stdout, process.stderr]) {
  const write = stream.write.bind(stream) as (chunk: unknown, ...rest: unknown[]) => boolean;
  stream.write = ((chunk: unknown, ...rest: unknown[]) => (typeof chunk === "string" && chunk.startsWith('{"ts":') ? true : write(chunk, ...rest))) as typeof stream.write;
}
