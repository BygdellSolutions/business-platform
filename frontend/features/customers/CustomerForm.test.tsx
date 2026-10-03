import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { CustomerForm } from "@/features/customers/CustomerForm";
import { networkError, normalizeError, type ApiResult } from "@/lib/api/errors";
import type { Customer } from "@/lib/api/types";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";
import { EMPTY_PROFILE } from "@/lib/profile";

const A = "00000000-0000-4000-8000-0000000000a1";
const B = "00000000-0000-4000-8000-0000000000b2";
const mocked = vi.mocked(apiFetch);

function customer(overrides: Partial<Customer> = {}): Customer {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    customer_type: "person",
    name: "Anna Andersson",
    email: "anna@example.test",
    phone: null,
    active: true,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    ...EMPTY_PROFILE,
    ...overrides,
  };
}
const ok = <T,>(data: T, status = 200): ApiResult<T> => ({ ok: true, status, data });
const fail = <T,>(status: number, body: unknown): ApiResult<T> => ({ ok: false, error: normalizeError(status, body) });

function mount(orgId: string, existing?: Customer) {
  return render(
    <OrgScope orgId={orgId}>
      <CustomerForm customer={existing} />
    </OrgScope>,
  );
}

beforeEach(() => {
  mocked.mockReset();
  router.push.mockReset();
  router.refresh.mockReset();
});

describe("creating a customer", () => {
  it("posts the typed values for the organization of the scope, with no organization_id, and opens the new record", async () => {
    mocked.mockResolvedValue(ok(customer({ id: "new-id" }), 201));
    mount(A);

    await userEvent.selectOptions(screen.getByLabelText("Type"), "company");
    await userEvent.type(screen.getByLabelText("Name"), "Umeå HK");
    await userEvent.type(screen.getByLabelText("Email"), "info@umea.test");
    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));

    expect(mocked).toHaveBeenCalledTimes(1);
    const [orgId, path, request] = mocked.mock.calls[0];
    expect(orgId).toBe(A);
    expect(path).toBe("/customers");
    expect(request).toEqual({
      method: "POST",
      body: { customer_type: "company", name: "Umeå HK", email: "info@umea.test", phone: null, active: true, ...EMPTY_PROFILE },
    });
    expect(JSON.stringify(request?.body)).not.toContain("organization");
    expect(router.push).toHaveBeenCalledWith(`/o/${A}/customers/new-id?created=1`);
    expect(router.refresh).toHaveBeenCalledTimes(1); // so Back to an earlier list is not served from the router cache
  });

  it("sends a blank email and phone as no value, and respects the Active checkbox", async () => {
    mocked.mockResolvedValue(ok(customer(), 201));
    mount(A);

    await userEvent.type(screen.getByLabelText("Name"), "Anna");
    await userEvent.type(screen.getByLabelText("Phone"), "   ");
    await userEvent.click(screen.getByLabelText("Active"));
    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ customer_type: "person", name: "Anna", email: null, phone: null, active: false, ...EMPTY_PROFILE });
  });

  it("shows each 422 on the matching control and keeps what was typed", async () => {
    mocked.mockResolvedValue(
      fail(422, {
        detail: [
          { loc: ["body", "name"], msg: "String should have at least 1 character", type: "string_too_short" },
          { loc: ["body", "email"], msg: "String should have at most 320 characters", type: "string_too_long" },
        ],
      }),
    );
    mount(A);
    await userEvent.type(screen.getByLabelText("Phone"), "070-1");

    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));

    expect(await screen.findByTestId("error-name")).toHaveTextContent("String should have at least 1 character");
    expect(screen.getByTestId("error-email")).toHaveTextContent("at most 320");
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Phone")).toHaveValue("070-1");
    expect(screen.queryByTestId("error-phone")).toBeNull();
    expect(screen.queryByTestId("form-error")).toBeNull();
    expect(router.push).not.toHaveBeenCalled();
  });

  it("shows a 422 about an unknown field instead of dropping it", async () => {
    mocked.mockResolvedValue(fail(422, { detail: [{ loc: ["body", "organization_id"], msg: "Extra inputs are not permitted", type: "extra_forbidden" }] }));
    mount(A);

    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));

    expect(await screen.findByTestId("form-error")).toHaveTextContent("organization_id: Extra inputs are not permitted");
  });

  it.each([
    [fail<Customer>(403, { detail: "Your role does not allow this." }), "Your role does not allow this."],
    [fail<Customer>(404, { detail: "Not found" }), "no longer exists, or you do not have access"],
    [fail<Customer>(409, { detail: "Conflicts with the current state" }), "Conflicts with the current state"],
    [fail<Customer>(500, { detail: "Traceback" }), "could not complete the request"],
    [{ ok: false, error: networkError() } as ApiResult<Customer>, "Could not reach the server"],
  ])("shows %# as a form-level message, stays on the form and can be retried", async (result, text) => {
    mocked.mockResolvedValueOnce(result).mockResolvedValueOnce(ok(customer({ id: "after-retry" }), 201));
    mount(A);
    await userEvent.type(screen.getByLabelText("Name"), "Anna");

    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));
    expect(await screen.findByTestId("form-error")).toHaveTextContent(text);
    expect(screen.getByLabelText("Name")).toHaveValue("Anna");
    expect(router.push).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/o/${A}/customers/after-retry?created=1`));
    expect(screen.queryByTestId("form-error")).toBeNull();
  });

  it("sends once however often the button is pressed while the request is running", async () => {
    let finish!: (result: ApiResult<Customer>) => void;
    mocked.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    mount(A);
    await userEvent.type(screen.getByLabelText("Name"), "Anna");

    const button = screen.getByRole("button", { name: "Create customer" });
    await userEvent.click(button);
    await userEvent.click(screen.getByRole("button", { name: "Saving…" }));
    await userEvent.type(screen.getByLabelText("Name"), "{Enter}"); // Enter submits the form too

    expect(mocked).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    await act(async () => finish(ok(customer(), 201)));
  });
});

describe("the billing profile", () => {
  it("has a box for each of the seven optional fields, empty for a new customer", () => {
    mount(A);
    for (const label of ["Address line 1", "Address line 2", "Postal code", "City", "Country code", "Registration number", "VAT number"]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
  });

  it("sends what was typed, as typed, and blank boxes as null", async () => {
    mocked.mockResolvedValue(ok(customer(), 201));
    mount(A);

    await userEvent.type(screen.getByLabelText("Name"), "Umeå HK");
    await userEvent.type(screen.getByLabelText("Address line 1"), "Ridvägen 2");
    await userEvent.type(screen.getByLabelText("Address line 2"), "   ");
    await userEvent.type(screen.getByLabelText("Country code"), "se"); // case and shape are the backend's business
    await userEvent.type(screen.getByLabelText("VAT number"), "SE802000000101");
    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));

    expect(mocked.mock.calls[0][2]?.body).toMatchObject({
      address_line1: "Ridvägen 2",
      address_line2: null,
      country_code: "se",
      vat_number: "SE802000000101",
      city: null,
    });
  });

  it("shows a backend refusal next to the box it is about and keeps what was typed", async () => {
    mocked.mockResolvedValue(fail(422, { detail: [{ loc: ["body", "country_code"], msg: "String should match pattern", type: "string_pattern_mismatch" }] }));
    mount(A);
    await userEvent.type(screen.getByLabelText("Country code"), "SWE");

    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));

    expect(await screen.findByTestId("error-country_code")).toHaveTextContent("String should match pattern");
    expect(screen.getByLabelText("Country code")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Country code")).toHaveValue("SWE");
    expect(screen.queryByTestId("form-error")).toBeNull();
  });

  it("shows the saved profile and sends only the profile fields that changed", async () => {
    const saved = customer({ city: "Umeå", country_code: "SE", vat_number: "SE1" });
    mocked.mockResolvedValue(ok({ ...saved, city: "Luleå" }));
    mount(A, saved);
    expect(screen.getByLabelText("City")).toHaveValue("Umeå");

    await userEvent.clear(screen.getByLabelText("City"));
    await userEvent.type(screen.getByLabelText("City"), "Luleå");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked.mock.calls[0][2]).toEqual({ method: "PATCH", body: { city: "Luleå" } });
    expect(await screen.findByTestId("saved")).toBeInTheDocument();
  });

  it("clearing a saved field sends null", async () => {
    const saved = customer({ vat_number: "SE1" });
    mocked.mockResolvedValue(ok({ ...saved, vat_number: null }));
    mount(A, saved);

    await userEvent.clear(screen.getByLabelText("VAT number"));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ vat_number: null });
  });

  it("shows what the backend stored after saving (normalized)", async () => {
    mocked.mockResolvedValue(ok(customer({ country_code: "SE" })));
    mount(A, customer());
    await userEvent.type(screen.getByLabelText("Country code"), "se");

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.getByLabelText("Country code")).toHaveValue("SE"));
  });
});

describe("editing a customer", () => {
  it("shows the saved values and sends only what changed", async () => {
    const saved = customer({ name: "Anna A", updated_at: "2026-10-02T10:00:00Z" });
    mocked.mockResolvedValue(ok(saved));
    mount(A, customer());
    expect(screen.getByLabelText("Name")).toHaveValue("Anna Andersson");
    expect(screen.getByLabelText("Email")).toHaveValue("anna@example.test");

    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Anna A");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked).toHaveBeenCalledWith(A, "/customers/11111111-1111-4111-8111-111111111111", { method: "PATCH", body: { name: "Anna A" } });
    expect(await screen.findByTestId("saved")).toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("clearing the email sends null, not an empty string", async () => {
    mocked.mockResolvedValue(ok(customer({ email: null })));
    mount(A, customer());

    await userEvent.clear(screen.getByLabelText("Email"));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ email: null });
  });

  it("makes no request when nothing changed", async () => {
    mount(A, customer());

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked).not.toHaveBeenCalled();
    expect(screen.getByTestId("unchanged")).toBeInTheDocument();
  });

  it("keeps the draft when the save fails, and a later successful save sends the same change", async () => {
    mocked.mockResolvedValueOnce(fail(422, { detail: [{ loc: ["body", "name"], msg: "String should have at least 1 character", type: "x" }] }));
    mocked.mockResolvedValueOnce(ok(customer({ name: "Anna B" })));
    mount(A, customer());
    await userEvent.clear(screen.getByLabelText("Name"));

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByTestId("error-name")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("");

    await userEvent.type(screen.getByLabelText("Name"), "Anna B");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByTestId("saved")).toBeInTheDocument();
    expect(screen.queryByTestId("error-name")).toBeNull();
  });

  it("a record that vanished (or was never in this organization) says so in the same words either way", async () => {
    mocked.mockResolvedValue(fail(404, { detail: "Not found" }));
    mount(A, customer());
    await userEvent.type(screen.getByLabelText("Name"), " Jr");

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByTestId("form-error")).toHaveTextContent("This record no longer exists, or you do not have access to it.");
  });

  it("deactivates and reactivates with one request each, leaving unsaved edits alone", async () => {
    mocked.mockResolvedValueOnce(ok(customer({ active: false, updated_at: "2026-10-02T10:00:00Z" })));
    mocked.mockResolvedValueOnce(ok(customer({ active: true, updated_at: "2026-10-03T10:00:00Z" })));
    mount(A, customer());
    await userEvent.type(screen.getByLabelText("Phone"), "070-123");

    await userEvent.click(screen.getByRole("button", { name: "Deactivate customer" }));

    expect(mocked).toHaveBeenLastCalledWith(A, "/customers/11111111-1111-4111-8111-111111111111", { method: "PATCH", body: { active: false } });
    expect(await screen.findByRole("button", { name: "Reactivate customer" })).toBeInTheDocument();
    expect(screen.getByTestId("status")).toHaveTextContent("Inactive");
    expect(screen.getByLabelText("Phone")).toHaveValue("070-123"); // the draft survived

    await userEvent.click(screen.getByRole("button", { name: "Reactivate customer" }));

    expect(mocked).toHaveBeenLastCalledWith(A, "/customers/11111111-1111-4111-8111-111111111111", { method: "PATCH", body: { active: true } });
    expect(await screen.findByRole("button", { name: "Deactivate customer" })).toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalledTimes(2);
  });

  it("a failed deactivation changes nothing on screen and says why", async () => {
    mocked.mockResolvedValue(fail(403, { detail: "Your role does not allow this." }));
    mount(A, customer());

    await userEvent.click(screen.getByRole("button", { name: "Deactivate customer" }));

    expect(await screen.findByTestId("form-error")).toHaveTextContent("Your role does not allow this.");
    expect(screen.getByTestId("status")).toHaveTextContent("Active");
    expect(router.refresh).not.toHaveBeenCalled();
  });
});

describe("organization scope", () => {
  it("a draft typed in one organization does not exist after the organization changes", async () => {
    const { rerender } = mount(A);
    await userEvent.type(screen.getByLabelText("Name"), "Typed in A");
    await userEvent.type(screen.getByLabelText("Email"), "a@a.test");
    expect(screen.getByLabelText("Name")).toHaveValue("Typed in A");

    rerender(
      <OrgScope orgId={B}>
        <CustomerForm />
      </OrgScope>,
    );

    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText("Email")).toHaveValue("");
  });

  it("saves into the organization it is shown for, never the previous one", async () => {
    mocked.mockResolvedValue(ok(customer(), 201));
    const { rerender } = mount(A);
    rerender(
      <OrgScope orgId={B}>
        <CustomerForm />
      </OrgScope>,
    );

    await userEvent.type(screen.getByLabelText("Name"), "Anna");
    await userEvent.click(screen.getByRole("button", { name: "Create customer" }));

    expect(mocked.mock.calls[0][0]).toBe(B);
    expect(router.push).toHaveBeenCalledWith(expect.stringContaining(`/o/${B}/`));
  });

  it("refuses to render outside an organization scope", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<CustomerForm />)).toThrow("useOrgId must be used inside <OrgScope>");
    quiet.mockRestore();
  });
});
