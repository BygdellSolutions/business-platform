import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { EarlierTransactions } from "@/features/settings/EarlierTransactions";
import { OrganizationSettings } from "@/features/settings/OrganizationSettings";
import { networkError, normalizeError, type ApiResult } from "@/lib/api/errors";
import type { AssignCurrencyResult, CurrencyStatus, Organization } from "@/lib/api/types";
import { EMPTY_PROFILE } from "@/lib/profile";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const A = "00000000-0000-4000-8000-0000000000a1";
const B = "00000000-0000-4000-8000-0000000000b2";
const mocked = vi.mocked(apiFetch);

function organization(overrides: Partial<Organization> = {}): Organization {
  return {
    id: A,
    name: "Fredrik Horse Therapy",
    legal_name: null,
    default_currency: "SEK",
    default_currency_locked: false,
    default_currency_lock_reason: null,
    timezone: null,
    today: "2026-10-08",
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    ...EMPTY_PROFILE,
    ...overrides,
  };
}
const ok = <T,>(data: T, status = 200): ApiResult<T> => ({ ok: true, status, data });
const fail = <T,>(status: number, body: unknown): ApiResult<T> => ({ ok: false, error: normalizeError(status, body) });

function mount(orgId: string, org: Organization, canEdit = true) {
  return render(
    <OrgScope orgId={orgId}>
      <OrganizationSettings organization={org} canEdit={canEdit} />
    </OrgScope>,
  );
}

beforeEach(() => {
  mocked.mockReset();
  router.push.mockReset();
  router.refresh.mockReset();
});

describe("who sees the form", () => {
  it("gives an owner or admin the editing form", () => {
    mount(A, organization());
    expect(screen.getByRole("form", { name: "Organization settings" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeInTheDocument();
    expect(screen.queryByTestId("read-only")).toBeNull();
  });

  it("shows everyone else the same values read-only, with no input and no save button", () => {
    mount(A, organization({ legal_name: "Fredrik Horse Therapy AB", city: "Umeå" }), false);

    expect(screen.getByTestId("read-only")).toBeInTheDocument();
    expect(screen.getByTestId("setting-legal_name")).toHaveTextContent("Fredrik Horse Therapy AB");
    expect(screen.getByTestId("setting-city")).toHaveTextContent("Umeå");
    expect(screen.getByTestId("setting-default_currency")).toHaveTextContent("SEK");
    expect(screen.getByTestId("setting-vat_number")).toHaveTextContent("Not set");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("never sends anything from the read-only view", () => {
    mount(A, organization(), false);
    expect(mocked).not.toHaveBeenCalled();
  });
});

describe("saving", () => {
  it("shows the saved values in their boxes", () => {
    mount(A, organization({ legal_name: "Acme AB", city: "Umeå", country_code: "SE" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Fredrik Horse Therapy");
    expect(screen.getByLabelText("Legal name")).toHaveValue("Acme AB");
    expect(screen.getByLabelText("Default currency")).toHaveValue("SEK");
    expect(screen.getByLabelText("City")).toHaveValue("Umeå");
    expect(screen.getByLabelText("Country code")).toHaveValue("SE");
  });

  it("sends only what changed, to the organization of the scope, with no organization_id", async () => {
    mocked.mockResolvedValue(ok(organization({ city: "Luleå" })));
    mount(A, organization());

    await userEvent.type(screen.getByLabelText("City"), "Luleå");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(mocked).toHaveBeenCalledTimes(1);
    expect(mocked).toHaveBeenCalledWith(A, "/organization", { method: "PATCH", body: { city: "Luleå" } });
    expect(await screen.findByTestId("saved")).toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("makes no request when nothing changed", async () => {
    mount(A, organization());
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(mocked).not.toHaveBeenCalled();
    expect(screen.getByTestId("unchanged")).toBeInTheDocument();
  });

  it("clearing the legal name or a profile field sends null, not an empty string", async () => {
    const saved = organization({ legal_name: "Acme AB", vat_number: "SE1" });
    mocked.mockResolvedValue(ok(organization()));
    mount(A, saved);

    await userEvent.clear(screen.getByLabelText("Legal name"));
    await userEvent.clear(screen.getByLabelText("VAT number"));
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ legal_name: null, vat_number: null });
  });

  it("sets the first currency as typed and shows the normalized answer", async () => {
    mocked.mockResolvedValue(ok(organization({ default_currency: "EUR" })));
    mount(A, organization({ default_currency: null }));

    await userEvent.type(screen.getByLabelText("Default currency"), "eur");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ default_currency: "eur" });
    await waitFor(() => expect(screen.getByLabelText("Default currency")).toHaveValue("EUR"));
  });

  it("shows a 422 next to its box and keeps what was typed", async () => {
    mocked.mockResolvedValue(fail(422, { detail: [{ loc: ["body", "default_currency"], msg: "String should match pattern", type: "x" }] }));
    mount(A, organization({ default_currency: null }));
    await userEvent.type(screen.getByLabelText("Default currency"), "EURO");

    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByTestId("error-default_currency")).toHaveTextContent("String should match pattern");
    expect(screen.getByLabelText("Default currency")).toHaveValue("EURO");
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it.each([
    [fail<Organization>(403, { detail: "Your role in this organization does not allow this action" }), "does not allow this action"],
    [fail<Organization>(500, { detail: "Traceback" }), "could not complete the request"],
    [{ ok: false, error: networkError() } as ApiResult<Organization>, "Could not reach the server"],
  ])("shows failure %# as a message and can be retried with the draft intact", async (result, text) => {
    mocked.mockResolvedValueOnce(result).mockResolvedValueOnce(ok(organization({ city: "X" })));
    mount(A, organization());
    await userEvent.type(screen.getByLabelText("City"), "X");

    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByTestId("form-error")).toHaveTextContent(text);
    expect(screen.getByLabelText("City")).toHaveValue("X");

    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByTestId("saved")).toBeInTheDocument();
  });

  it("sends once however often the button is pressed while the request runs", async () => {
    let finish!: (result: ApiResult<Organization>) => void;
    mocked.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    mount(A, organization());
    await userEvent.type(screen.getByLabelText("City"), "X");

    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await userEvent.click(screen.getByRole("button", { name: "Saving…" }));
    await userEvent.type(screen.getByLabelText("City"), "{Enter}");

    expect(mocked).toHaveBeenCalledTimes(1);
    await act(async () => finish(ok(organization({ city: "X" }))));
  });
});

describe("single flight", () => {
  it("two submits in the very same instant send one request (the guard, not only the disabled button)", async () => {
    let finish!: (result: ApiResult<Organization>) => void;
    mocked.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    mount(A, organization());
    await userEvent.type(screen.getByLabelText("City"), "X");
    const form = screen.getByRole("form", { name: "Organization settings" });

    // Both events land before React re-renders, so the button is not disabled yet for the second.
    await act(async () => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });

    expect(mocked).toHaveBeenCalledTimes(1);
    await act(async () => finish(ok(organization({ city: "X" }))));
  });
});

describe("the default currency", () => {
  it("explains that none is set and that transactions need one", () => {
    mount(A, organization({ default_currency: null }));
    expect(screen.getByLabelText("Default currency")).toHaveValue("");
    expect(screen.getByText(/Transactions cannot be created until a currency is set/)).toBeInTheDocument();
    expect(screen.getByLabelText("Default currency")).toBeEnabled();
  });

  it("is switched off, with the reason, once prices exist", () => {
    mount(A, organization({ default_currency_locked: true, default_currency_lock_reason: "The catalog already has items." }));
    expect(screen.getByLabelText("Default currency")).toBeDisabled();
    expect(screen.getByText(/can no longer be changed because prices already exist/)).toBeInTheDocument();
    expect(screen.getByText(/The catalog already has items/)).toBeInTheDocument();
  });

  it("still saves other settings while the currency is switched off, without sending the currency", async () => {
    mocked.mockResolvedValue(ok(organization({ default_currency_locked: true, city: "Umeå" })));
    mount(A, organization({ default_currency_locked: true }));

    await userEvent.type(screen.getByLabelText("City"), "Umeå");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ city: "Umeå" });
  });

  it("shows the backend's refusal when the currency was locked by something else in the meantime", async () => {
    mocked.mockResolvedValue(fail(409, { detail: { code: "currency_locked", message: "The default currency can no longer be changed. The catalog already has items." } }));
    mount(A, organization());
    await userEvent.clear(screen.getByLabelText("Default currency"));
    await userEvent.type(screen.getByLabelText("Default currency"), "EUR");

    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByTestId("form-error")).toHaveTextContent("can no longer be changed");
    expect(screen.getByLabelText("Default currency")).toHaveValue("EUR");
  });
});

describe("the time zone", () => {
  it("explains that none is set, that dates default to UTC, and what today is", () => {
    mount(A, organization());
    expect(screen.getByLabelText("Time zone")).toHaveValue("");
    expect(screen.getByText(/Not set: new dates default to today in UTC \(2026-10-08\)/)).toBeInTheDocument();
  });

  it("offers the browser's zone names as suggestions, never as a rule", () => {
    mount(A, organization());
    const input = screen.getByLabelText("Time zone");
    const list = document.getElementById(input.getAttribute("list") ?? "");
    expect(list?.tagName).toBe("DATALIST");
    expect(Array.from(list?.querySelectorAll("option") ?? []).map((o) => o.getAttribute("value"))).toContain("Europe/Stockholm");
  });

  it("sends only the zone when only the zone changed", async () => {
    mocked.mockResolvedValueOnce(ok(organization({ timezone: "Europe/Stockholm" })));
    mount(A, organization());
    await userEvent.type(screen.getByLabelText("Time zone"), "Europe/Stockholm");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(mocked).toHaveBeenCalledWith(A, "/organization", { method: "PATCH", body: { timezone: "Europe/Stockholm" } });
  });

  it("clearing the zone sends null", async () => {
    mocked.mockResolvedValueOnce(ok(organization()));
    mount(A, organization({ timezone: "Europe/Stockholm" }));
    await userEvent.clear(screen.getByLabelText("Time zone"));
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(mocked).toHaveBeenCalledWith(A, "/organization", { method: "PATCH", body: { timezone: null } });
  });

  it("shows the backend's refusal of an unknown zone next to the box", async () => {
    mocked.mockResolvedValueOnce(fail(422, { detail: [{ loc: ["body", "timezone"], msg: "Value error, must be an IANA time zone name such as Europe/Stockholm", type: "value_error" }] }));
    mount(A, organization());
    await userEvent.type(screen.getByLabelText("Time zone"), "Mars/Olympus");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText(/must be an IANA time zone name/)).toBeInTheDocument();
    expect(screen.getByLabelText("Time zone")).toHaveValue("Mars/Olympus");
  });

  it("is shown read-only to everyone else", () => {
    mount(A, organization({ timezone: "Europe/Stockholm" }), false);
    expect(screen.getByTestId("setting-timezone")).toHaveTextContent("Europe/Stockholm");
  });
});

describe("organization scope", () => {
  it("talks only to the organization of its scope, and a different scope starts from different values", async () => {
    mocked.mockResolvedValue(ok(organization({ id: B, city: "B-stad" })));
    const { unmount } = mount(B, organization({ id: B, name: "Other Org" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Other Org");
    await userEvent.type(screen.getByLabelText("City"), "B-stad");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(mocked.mock.calls[0][0]).toBe(B);
    unmount();

    mount(A, organization());
    expect(screen.getByLabelText("Name")).toHaveValue("Fredrik Horse Therapy");
    expect(screen.getByLabelText("City")).toHaveValue("");
  });
});

// --- transactions that predate currencies ------------------------------------------------------------------------------

function mountEarlier(status: CurrencyStatus, canEdit = true, orgId = A) {
  return render(
    <OrgScope orgId={orgId}>
      <EarlierTransactions status={status} canEdit={canEdit} />
    </OrgScope>,
  );
}

describe("transactions without a currency", () => {
  it("shows nothing when every transaction has a currency", () => {
    const { container } = mountEarlier({ default_currency: "SEK", transactions_without_currency: 0 });
    expect(container).toBeEmptyDOMElement();
  });

  it("explains that they have none and asks for the currency to be set first when it is not", () => {
    mountEarlier({ default_currency: null, transactions_without_currency: 3 });
    expect(screen.getByTestId("earlier-count")).toHaveTextContent("3 transactions were created before currencies existed and have no currency");
    expect(screen.getByText(/Set the organization.s default currency first/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers an owner or admin the explicit, confirmed assignment, and sends the organization's own currency", async () => {
    mocked.mockResolvedValue(ok<AssignCurrencyResult>({ currency: "SEK", assigned: 3 }));
    mountEarlier({ default_currency: "SEK", transactions_without_currency: 3 });

    await userEvent.click(screen.getByTestId("assign-currency"));
    expect(mocked).not.toHaveBeenCalled(); // the first click only asks
    expect(screen.getByText(/Assign SEK to 3 transactions\? This cannot be undone\./)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("assign-currency-confirm"));

    expect(mocked).toHaveBeenCalledTimes(1);
    expect(mocked).toHaveBeenCalledWith(A, "/transactions/assign-currency", { method: "POST", body: { currency: "SEK" } });
    expect(await screen.findByTestId("assigned")).toHaveTextContent("3 transactions now have the currency SEK");
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("assigns the organization's own currency, whatever it is, never a fixed one", async () => {
    mocked.mockResolvedValue(ok<AssignCurrencyResult>({ currency: "EUR", assigned: 2 }));
    mountEarlier({ default_currency: "EUR", transactions_without_currency: 2 });

    expect(screen.getByTestId("assign-currency")).toHaveTextContent("Assign EUR to these transactions");
    await userEvent.click(screen.getByTestId("assign-currency"));
    await userEvent.click(screen.getByTestId("assign-currency-confirm"));

    expect(mocked.mock.calls[0][2]).toEqual({ method: "POST", body: { currency: "EUR" } });
    expect(await screen.findByTestId("assigned")).toHaveTextContent("currency EUR");
  });

  it("two confirmations in the very same instant assign once", async () => {
    let finish!: (result: ApiResult<AssignCurrencyResult>) => void;
    mocked.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    mountEarlier({ default_currency: "SEK", transactions_without_currency: 2 });
    await userEvent.click(screen.getByTestId("assign-currency"));
    const confirm = screen.getByTestId("assign-currency-confirm");

    await act(async () => {
      fireEvent.click(confirm);
      fireEvent.click(confirm);
    });

    expect(mocked).toHaveBeenCalledTimes(1);
    await act(async () => finish(ok<AssignCurrencyResult>({ currency: "SEK", assigned: 2 })));
  });

  it("does nothing when the question is declined", async () => {
    mountEarlier({ default_currency: "SEK", transactions_without_currency: 1 });
    await userEvent.click(screen.getByTestId("assign-currency"));
    await userEvent.click(screen.getByTestId("assign-currency-keep"));
    expect(mocked).not.toHaveBeenCalled();
    expect(screen.getByTestId("earlier-count")).toHaveTextContent("1 transaction was created");
  });

  it("offers no action to anyone else", () => {
    mountEarlier({ default_currency: "SEK", transactions_without_currency: 2 }, false);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/An owner or admin can assign/)).toBeInTheDocument();
  });

  it("shows the backend's refusal and can be retried", async () => {
    mocked.mockResolvedValueOnce(fail(403, { detail: "Your role in this organization does not allow this action" }));
    mocked.mockResolvedValueOnce(ok<AssignCurrencyResult>({ currency: "SEK", assigned: 1 }));
    mountEarlier({ default_currency: "SEK", transactions_without_currency: 1 });

    await userEvent.click(screen.getByTestId("assign-currency"));
    await userEvent.click(screen.getByTestId("assign-currency-confirm"));
    expect(await screen.findByTestId("form-error")).toHaveTextContent("does not allow this action");
    expect(router.refresh).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("assign-currency"));
    await userEvent.click(screen.getByTestId("assign-currency-confirm"));
    expect(await screen.findByTestId("assigned")).toHaveTextContent("1 transaction now has the currency SEK");
  });
});
