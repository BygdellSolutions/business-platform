import { beforeEach, describe, expect, it, vi } from "vitest";

import { networkError } from "@/lib/api/errors";
import type { Customer } from "@/lib/api/types";
import { CUSTOMER_CHOICES, customerEntity, customerSearch } from "@/features/customers/customer-picker";

vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";
import { EMPTY_PROFILE } from "@/lib/profile";

const A = "00000000-0000-4000-8000-0000000000a1";
const mocked = vi.mocked(apiFetch);
const signal = new AbortController().signal;

function customer(overrides: Partial<Customer>): Customer {
  return { id: "1", customer_type: "person", name: "Anna", email: null, phone: null, active: true, created_at: "", updated_at: "", ...EMPTY_PROFILE, ...overrides };
}

beforeEach(() => {
  mocked.mockReset();
});

describe("customerSearch", () => {
  it("asks the BFF for the organization it was made for, active customers only for a new assignment", async () => {
    mocked.mockResolvedValue({ ok: true, status: 200, data: [] });

    await customerSearch(A, { activeOnly: true })("anna", signal);

    const [orgId, path, request] = mocked.mock.calls[0];
    expect(orgId).toBe(A);
    const url = new URL(path, "http://x");
    expect(url.pathname).toBe("/customers");
    expect(url.searchParams.get("active")).toBe("true");
    expect(url.searchParams.get("q")).toBe("anna");
    expect(url.searchParams.get("limit")).toBe(String(CUSTOMER_CHOICES));
    expect(request).toEqual({ signal });
    expect(path).not.toContain("organization");
  });

  it("a filter asks for all customers, inactive ones included, and leaves q out when empty", async () => {
    mocked.mockResolvedValue({ ok: true, status: 200, data: [] });

    await customerSearch(A, { activeOnly: false })("", signal);

    const url = new URL(mocked.mock.calls[0][1], "http://x");
    expect(url.searchParams.has("active")).toBe(false);
    expect(url.searchParams.has("q")).toBe(false);
  });

  it("encodes what the user typed, so it cannot add parameters", async () => {
    mocked.mockResolvedValue({ ok: true, status: 200, data: [] });

    await customerSearch(A, { activeOnly: true })("a&active=false&limit=999", signal);

    const url = new URL(mocked.mock.calls[0][1], "http://x");
    expect(url.searchParams.get("q")).toBe("a&active=false&limit=999");
    expect(url.searchParams.get("active")).toBe("true");
    expect(url.searchParams.get("limit")).toBe(String(CUSTOMER_CHOICES));
  });

  it("turns customers into entities identified by id, with the email as a detail", async () => {
    mocked.mockResolvedValue({
      ok: true,
      status: 200,
      data: [customer({ id: "x", name: "Anna", email: "a@x.test" }), customer({ id: "y", name: "Umeå HK", customer_type: "company", active: false })],
    });

    const result = await customerSearch(A, { activeOnly: false })("", signal);

    expect(result).toEqual({
      ok: true,
      status: 200,
      data: [
        { id: "x", label: "Anna", detail: "a@x.test", inactive: false },
        { id: "y", label: "Umeå HK", detail: "company", inactive: true },
      ],
    });
  });

  it("passes a failure through unchanged", async () => {
    const failure = { ok: false as const, error: networkError() };
    mocked.mockResolvedValue(failure);
    expect(await customerSearch(A, { activeOnly: true })("", signal)).toBe(failure);
  });
});

describe("customerEntity", () => {
  it("shows a referenced customer, flagging a deactivated one", () => {
    expect(customerEntity({ id: "x", name: "Anna", active: true })).toEqual({ id: "x", label: "Anna", inactive: false });
    expect(customerEntity({ id: "y", name: "Old", active: false })).toEqual({ id: "y", label: "Old", inactive: true });
  });
});
