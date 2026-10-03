import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { normalizeError, networkError, type ApiResult } from "@/lib/api/errors";
import { NO_PROBLEMS, problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["name", "price_ex_vat"] as const;

describe("problemsFrom", () => {
  it("has nothing to say without an error", () => {
    expect(problemsFrom(null, CONTROLS)).toBe(NO_PROBLEMS);
  });

  it("puts a 422 on the control whose name equals the API field", () => {
    const error = normalizeError(422, {
      detail: [
        { loc: ["body", "name"], msg: "String should have at least 1 character", type: "x" },
        { loc: ["body", "price_ex_vat"], msg: "Value error, must be a non-negative decimal", type: "x" },
      ],
    });

    expect(problemsFrom(error, CONTROLS)).toEqual({
      byField: { name: ["String should have at least 1 character"], price_ex_vat: ["must be a non-negative decimal"] },
      general: [],
    });
  });

  it("never loses a message that has no control: it goes to the general list with its path", () => {
    const error = normalizeError(422, {
      detail: [
        { loc: ["body", "organization_id"], msg: "Extra inputs are not permitted", type: "x" },
        { loc: ["body"], msg: "Value error, whole body", type: "x" },
        { loc: ["body", "name"], msg: "Field required", type: "x" },
      ],
    });

    const problems = problemsFrom(error, CONTROLS);

    expect(problems.byField).toEqual({ name: ["Field required"] });
    expect(problems.general).toEqual(["whole body", "organization_id: Extra inputs are not permitted"]);
  });

  it("describes a 404 the same way whatever the reason, without naming another organization", () => {
    const missing = problemsFrom(normalizeError(404, { detail: "Not found" }), CONTROLS);
    const foreign = problemsFrom(normalizeError(404, { detail: "anything else the backend might say" }), CONTROLS);
    expect(missing).toEqual(foreign);
    expect(missing.general).toEqual(["This record no longer exists, or you do not have access to it."]);
  });

  it.each([
    [normalizeError(403, { detail: "Your role does not allow this." }), "Your role does not allow this."],
    [normalizeError(409, { detail: "Item is referenced by other records" }), "Item is referenced by other records"],
    [normalizeError(500, { detail: "Traceback" }), "The server could not complete the request. Try again."],
    [networkError(), "Could not reach the server. Check your connection and try again."],
  ])("shows %# as one general message", (error, message) => {
    expect(problemsFrom(error, CONTROLS)).toEqual({ byField: {}, general: [message] });
  });
});

describe("useMutation", () => {
  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((r) => {
      resolve = r;
    });
    return { promise, resolve };
  }

  it("returns the data on success", async () => {
    const { result } = renderHook(() => useMutation());

    let data: string | null = null;
    await act(async () => {
      data = await result.current.run(async () => ({ ok: true, status: 200, data: "saved" }) as ApiResult<string>);
    });

    expect(data).toBe("saved");
    expect(result.current.error).toBeNull();
    expect(result.current.pending).toBe(false);
  });

  it("keeps the error on failure and clears it on the next attempt", async () => {
    const { result } = renderHook(() => useMutation());

    await act(async () => {
      await result.current.run(async () => ({ ok: false, error: networkError() }) as ApiResult<string>);
    });
    expect(result.current.error).toMatchObject({ kind: "network" });

    await act(async () => {
      await result.current.run(async () => ({ ok: true, status: 200, data: "ok" }) as ApiResult<string>);
    });
    expect(result.current.error).toBeNull();
  });

  it("runs one request at a time: a second call while one is in flight does nothing", async () => {
    const { result } = renderHook(() => useMutation());
    const first = deferred<ApiResult<string>>();
    let calls = 0;

    let firstRun!: Promise<string | null>;
    let secondResult: string | null | undefined;
    await act(async () => {
      firstRun = result.current.run(() => {
        calls += 1;
        return first.promise;
      });
      secondResult = await result.current.run(async () => {
        calls += 1;
        return { ok: true, status: 200, data: "second" } as ApiResult<string>;
      });
    });

    expect(result.current.pending).toBe(true);
    expect(secondResult).toBeNull();
    expect(calls).toBe(1);

    await act(async () => {
      first.resolve({ ok: true, status: 201, data: "first" });
      await firstRun;
    });
    expect(result.current.pending).toBe(false);
  });
});
