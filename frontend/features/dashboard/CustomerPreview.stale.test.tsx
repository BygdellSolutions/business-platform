import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/errors";
import type { Customer } from "@/lib/api/types";
import { CustomerPreview } from "@/features/dashboard/CustomerPreview";

/**
 * Defense in depth. OrgScope remounts the page when the organization changes, so a late
 * answer would normally land on an unmounted component. This test removes that safety net
 * (the SAME component instance sees the organization change) to prove the component itself
 * never displays an answer for an organization it has already left.
 */

const A = "00000000-0000-4000-8000-0000000000a1";
const B = "00000000-0000-4000-8000-0000000000b2";
let currentOrg = A;

vi.mock("@/components/shell/org-context", () => ({ useOrgId: () => currentOrg }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";
import { EMPTY_PROFILE } from "@/lib/profile";

const mocked = vi.mocked(apiFetch);

function customer(name: string): Customer {
  return { id: name, customer_type: "person", name, email: null, phone: null, active: true, created_at: "", updated_at: "", created_by: null, updated_by: null, ...EMPTY_PROFILE };
}
const ok = (customers: Customer[]): ApiResult<Customer[]> => ({ ok: true, status: 200, data: customers });

beforeEach(() => {
  mocked.mockReset();
  currentOrg = A;
});

describe("CustomerPreview without an OrgScope remount", () => {
  it("ignores a late answer for the organization it has left", async () => {
    let resolveA!: (value: ApiResult<Customer[]>) => void;
    mocked.mockImplementation((org) =>
      org === A ? new Promise<ApiResult<Customer[]>>((resolve) => { resolveA = resolve; }) : Promise.resolve(ok([customer("Only in B")])),
    );
    const { rerender } = render(<CustomerPreview />);

    currentOrg = B;
    rerender(<CustomerPreview />); // same instance, organization changed
    expect(await screen.findByText("Only in B")).toBeInTheDocument();
    await act(async () => resolveA(ok([customer("Secret of A")]))); // A answers after the switch

    expect(screen.queryByText("Secret of A")).toBeNull();
    expect(screen.getByText("Only in B")).toBeInTheDocument();
  });

  it("aborts the previous organization's request when the organization changes", async () => {
    const signals: Record<string, AbortSignal | undefined> = {};
    mocked.mockImplementation((org, _path, request) => {
      signals[org] = request?.signal;
      return new Promise(() => {});
    });
    const { rerender } = render(<CustomerPreview />);
    expect(signals[A]?.aborted).toBe(false);

    currentOrg = B;
    rerender(<CustomerPreview />);

    expect(signals[A]?.aborted).toBe(true);
    expect(signals[B]?.aborted).toBe(false);
  });
});
