import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api/errors";
import type { Customer } from "@/lib/api/types";
import { OrgScope } from "@/components/shell/org-context";
import { CustomerPreview } from "@/features/dashboard/CustomerPreview";

vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const A = "00000000-0000-4000-8000-0000000000a1";
const B = "00000000-0000-4000-8000-0000000000b2";
const mocked = vi.mocked(apiFetch);

function customer(name: string): Customer {
  return { id: name, customer_type: "person", name, email: null, phone: null, active: true, created_at: "", updated_at: "" };
}
const ok = (customers: Customer[]): ApiResult<Customer[]> => ({ ok: true, status: 200, data: customers });

function mount(orgId: string) {
  return render(<OrgScope orgId={orgId}><CustomerPreview /></OrgScope>);
}

beforeEach(() => {
  mocked.mockReset();
});

describe("CustomerPreview", () => {
  it("reads through the BFF for the organization of its scope", async () => {
    mocked.mockResolvedValue(ok([customer("Anna Andersson"), customer("Umeå HK")]));

    mount(A);

    expect(screen.getByTestId("customer-preview-loading")).toBeInTheDocument();
    expect(await screen.findAllByTestId("customer-preview-item")).toHaveLength(2);
    expect(mocked).toHaveBeenCalledWith(A, "/customers?limit=10&active=true", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("filters what it shows with client-side state", async () => {
    mocked.mockResolvedValue(ok([customer("Anna Andersson"), customer("Umeå HK")]));
    mount(A);
    await screen.findAllByTestId("customer-preview-item");

    await userEvent.type(screen.getByLabelText("Filter preview"), "umeå");

    expect(screen.getAllByTestId("customer-preview-item").map((li) => li.textContent)).toEqual(["Umeå HK"]);
    await userEvent.clear(screen.getByLabelText("Filter preview"));
    await userEvent.type(screen.getByLabelText("Filter preview"), "zzz");
    expect(screen.getByText("No customers match the filter.")).toBeInTheDocument();
  });

  it("says so when there are no customers", async () => {
    mocked.mockResolvedValue(ok([]));
    mount(A);
    expect(await screen.findByText("No customers yet.")).toBeInTheDocument();
  });

  it.each([
    [{ kind: "forbidden", status: 403, message: "Your role does not allow this." }, "Your role does not allow this."],
    [{ kind: "server", status: 502, message: "The server could not complete the request. Try again." }, "The server could not complete the request. Try again."],
    [{ kind: "network", status: 0, message: "Could not reach the server." }, "Could not reach the server."],
  ] as const)("shows an error for %j", async (error, message) => {
    mocked.mockResolvedValue({ ok: false, error } as ApiResult<Customer[]>);

    mount(A);

    expect(await screen.findByTestId("customer-preview-error")).toHaveTextContent(message);
  });

  it("cancels its request when the page is left", async () => {
    let signal: AbortSignal | undefined;
    mocked.mockImplementation((_org, _path, request) => {
      signal = request?.signal;
      return new Promise(() => {});
    });
    const { unmount } = mount(A);
    expect(signal?.aborted).toBe(false);

    unmount();

    expect(signal?.aborted).toBe(true);
  });

  it("never shows a late answer from a previous organization", async () => {
    let resolveA!: (value: ApiResult<Customer[]>) => void;
    mocked.mockImplementation((org) =>
      org === A ? new Promise<ApiResult<Customer[]>>((resolve) => { resolveA = resolve; }) : Promise.resolve(ok([customer("Only in B")])),
    );
    const { rerender } = mount(A);

    rerender(<OrgScope orgId={B}><CustomerPreview /></OrgScope>); // switch before A has answered
    expect(await screen.findByText("Only in B")).toBeInTheDocument();
    await act(async () => resolveA(ok([customer("Secret of A")]))); // A's answer arrives late

    expect(screen.queryByText("Secret of A")).toBeNull();
    expect(screen.getByText("Only in B")).toBeInTheDocument();
    expect(mocked.mock.calls.map((call) => call[0])).toEqual([A, B]);
  });

  it("starts from a clean slate in the new organization (filter text and list)", async () => {
    mocked.mockImplementation((org) => Promise.resolve(ok(org === A ? [customer("Anna A")] : [customer("Anna B")])));
    const { rerender } = mount(A);
    await screen.findByText("Anna A");
    await userEvent.type(screen.getByLabelText("Filter preview"), "Anna");

    rerender(<OrgScope orgId={B}><CustomerPreview /></OrgScope>);

    await waitFor(() => expect(screen.getByText("Anna B")).toBeInTheDocument());
    expect(screen.getByLabelText("Filter preview")).toHaveValue("");
    expect(screen.queryByText("Anna A")).toBeNull();
  });
});
