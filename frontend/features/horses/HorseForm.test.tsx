import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { HorseForm } from "@/features/horses/HorseForm";
import { networkError, normalizeError, type ApiResult } from "@/lib/api/errors";
import type { Customer, Horse } from "@/lib/api/types";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";
import { EMPTY_PROFILE } from "@/lib/profile";

const A = "00000000-0000-4000-8000-0000000000a1";
const B = "00000000-0000-4000-8000-0000000000b2";
const mocked = vi.mocked(apiFetch);

function customer(id: string, name: string, active = true): Customer {
  return { id, customer_type: "person", name, email: null, phone: null, active, created_at: "", updated_at: "", created_by: null, updated_by: null, ...EMPTY_PROFILE };
}
const ANNA = customer("11111111-1111-4111-8111-111111111111", "Anna Andersson");
const UMEA = customer("22222222-2222-4222-8222-222222222222", "Umeå HK");
const OLD = customer("33333333-3333-4333-8333-333333333333", "Old Owner", false);

function horse(overrides: Partial<Horse> = {}): Horse {
  return {
    id: "99999999-9999-4999-8999-999999999999",
    name: "Kalle",
    owner_customer_id: ANNA.id,
    stable_customer_id: UMEA.id,
    owner: { id: ANNA.id, name: ANNA.name, active: true },
    stable: { id: UMEA.id, name: UMEA.name, active: true },
    birth_year: 2012,
    sex: "gelding",
    breed: "Icelandic",
    active: true,
    created_at: "",
    updated_at: "",
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}

const ok = <T,>(data: T, status = 200): ApiResult<T> => ({ ok: true, status, data });
const fail = <T,>(status: number, body: unknown): ApiResult<T> => ({ ok: false, error: normalizeError(status, body) });
const referenceError = (field: string, message: string, type: string) => fail<Horse>(422, { detail: [{ loc: ["body", field], msg: message, type }] });

/** The backend for the form: customer searches answer from `customers`, anything else from `save`. */
function backend(save: (path: string, body: unknown) => ApiResult<unknown>, customers: Customer[] = [ANNA, UMEA, OLD]) {
  mocked.mockImplementation((async (_orgId: string, path: string, request?: { body?: unknown }) => {
    if (path.startsWith("/customers")) {
      const url = new URL(path, "http://x");
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      const activeOnly = url.searchParams.get("active") === "true";
      return ok(customers.filter((c) => c.name.toLowerCase().includes(q) && (!activeOnly || c.active)));
    }
    return save(path, request?.body);
  }) as typeof apiFetch);
}

const writes = () => mocked.mock.calls.filter(([, path]) => !path.startsWith("/customers"));
const lastWrite = () => writes().at(-1)!;
const picker = (name: string) => within(screen.getByTestId(`picker-${name}`));
const owner = () => picker("owner_customer_id");
const stable = () => picker("stable_customer_id");

async function choose(pickerTools: ReturnType<typeof picker>, name: RegExp | string) {
  await userEvent.click(pickerTools.getByRole("combobox"));
  await userEvent.click(await pickerTools.findByRole("option", { name }));
}

function mount(orgId: string, existing?: Horse) {
  return render(
    <OrgScope orgId={orgId}>
      <HorseForm horse={existing} />
    </OrgScope>,
  );
}

beforeEach(() => {
  mocked.mockReset();
  router.push.mockReset();
  router.refresh.mockReset();
});

describe("creating a horse", () => {
  it("sends the chosen customers by id, the other fields as typed, and no organization_id", async () => {
    backend(() => ok(horse({ id: "new-id" }), 201));
    mount(A);

    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Anna/);
    await choose(stable(), /Umeå/);
    await userEvent.type(screen.getByLabelText("Birth year"), "2012");
    await userEvent.selectOptions(screen.getByLabelText("Sex"), "gelding");
    await userEvent.type(screen.getByLabelText("Breed"), "Icelandic");
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    const [orgId, path, request] = lastWrite();
    expect(orgId).toBe(A);
    expect(path).toBe("/horses");
    expect(request).toEqual({
      method: "POST",
      body: { name: "Kalle", owner_customer_id: ANNA.id, stable_customer_id: UMEA.id, birth_year: 2012, sex: "gelding", breed: "Icelandic", active: true },
    });
    expect(JSON.stringify(request?.body)).not.toContain("organization");
    expect(router.push).toHaveBeenCalledWith(`/o/${A}/horses/new-id?created=1`);
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("the owner is not decided here: without one the field is left out and the backend's answer shows on the Owner control", async () => {
    backend(() => fail(422, { detail: [{ loc: ["body", "owner_customer_id"], msg: "Field required", type: "missing" }] }));
    mount(A);

    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    const body = lastWrite()[2]?.body as Record<string, unknown>;
    expect("owner_customer_id" in body).toBe(false);
    expect(await screen.findByTestId("error-owner_customer_id")).toHaveTextContent("Field required");
    expect(screen.queryByTestId("error-stable_customer_id")).toBeNull();
    expect(screen.getByRole("combobox", { name: "Owner" })).toHaveAttribute("aria-invalid", "true");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("the stable is optional: it is sent as null when none is chosen", async () => {
    backend(() => ok(horse({ stable: null, stable_customer_id: null }), 201));
    mount(A);

    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Anna/);
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    expect(lastWrite()[2]?.body).toMatchObject({ owner_customer_id: ANNA.id, stable_customer_id: null, birth_year: null, sex: null, breed: null });
  });

  it("owner and stable can be different customers, or the same one (the backend decides if it allows that)", async () => {
    backend(() => ok(horse(), 201));
    mount(A);
    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Different");
    await choose(owner(), /Anna/);
    await choose(stable(), /Umeå/);
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));
    expect(lastWrite()[2]?.body).toMatchObject({ owner_customer_id: ANNA.id, stable_customer_id: UMEA.id });

    mocked.mockClear();
    await choose(stable(), /Anna/); // now the same customer in both
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));
    expect(lastWrite()[2]?.body).toMatchObject({ owner_customer_id: ANNA.id, stable_customer_id: ANNA.id });
  });

  it("only active customers are asked for as new owner or stable", async () => {
    backend(() => ok(horse(), 201));
    mount(A);

    await userEvent.click(owner().getByRole("combobox"));
    expect(await owner().findAllByRole("option")).toHaveLength(2);
    expect(owner().queryByRole("option", { name: /Old Owner/ })).toBeNull();
    await userEvent.click(stable().getByRole("combobox"));
    await stable().findAllByRole("option");

    const searches = mocked.mock.calls.filter(([, path]) => path.startsWith("/customers"));
    expect(searches.length).toBeGreaterThanOrEqual(2);
    for (const [, path] of searches) expect(new URL(path, "http://x").searchParams.get("active")).toBe("true");
  });

  it("typing a customer's name without choosing it assigns nobody", async () => {
    backend(() => fail(422, { detail: [{ loc: ["body", "owner_customer_id"], msg: "Field required", type: "missing" }] }));
    mount(A);

    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await userEvent.type(owner().getByRole("combobox"), "Anna Andersson");
    await owner().findAllByRole("option");
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    expect("owner_customer_id" in (lastWrite()[2]?.body as object)).toBe(false);
  });

  it("can be created inactive", async () => {
    backend(() => ok(horse({ active: false }), 201));
    mount(A);
    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Anna/);
    await userEvent.click(screen.getByLabelText("Active"));
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));
    expect(lastWrite()[2]?.body).toMatchObject({ active: false });
  });
});

describe("the other fields are the backend's to judge", () => {
  it.each([
    ["2012", 2012],
    ["1850", 1850], // implausible: the backend refuses it, not this form
    ["2999", 2999], // in the future: same
    [" 2012 ", 2012],
    ["", null],
  ])("birth year %j is sent as %j, a JSON integer", async (typed, expected) => {
    backend(() => ok(horse(), 201));
    mount(A);
    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Anna/);
    if (typed !== "") await userEvent.type(screen.getByLabelText("Birth year"), typed);

    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    const year = (lastWrite()[2]?.body as { birth_year: unknown }).birth_year;
    expect(year).toBe(expected);
    if (expected !== null) expect(typeof year).toBe("number");
  });

  it.each(["abc", "20x2", "2012.5", "-5", "1e3", "99999999999999999999"])("a birth year that is not a whole number (%j) is stopped on its control, before any request", async (typed) => {
    backend(() => ok(horse(), 201));
    mount(A);
    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Anna/);
    await userEvent.type(screen.getByLabelText("Birth year"), typed);

    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    expect(screen.getByTestId("error-birth_year")).toHaveTextContent("Enter a whole number");
    expect(writes()).toHaveLength(0);
  });

  it("maps every 422 to its control, the owner and the stable included, and keeps the draft", async () => {
    backend(() =>
      fail(422, {
        detail: [
          { loc: ["body", "name"], msg: "String should have at least 1 character", type: "x" },
          { loc: ["body", "owner_customer_id"], msg: "Customer not found", type: "reference.not_found" },
          { loc: ["body", "stable_customer_id"], msg: "Customer is inactive", type: "reference.inactive" },
          { loc: ["body", "birth_year"], msg: "Input should be greater than or equal to 1900", type: "x" },
          { loc: ["body", "sex"], msg: "Input should be 'mare', 'stallion' or 'gelding'", type: "x" },
          { loc: ["body", "breed"], msg: "String should have at most 100 characters", type: "x" },
        ],
      }),
    );
    mount(A);
    await choose(owner(), /Anna/);
    await choose(stable(), /Umeå/);
    await userEvent.type(screen.getByLabelText("Birth year"), "1850");

    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    expect(await screen.findByTestId("error-name")).toHaveTextContent("at least 1 character");
    expect(screen.getByTestId("error-owner_customer_id")).toHaveTextContent("Customer not found");
    expect(screen.getByTestId("error-stable_customer_id")).toHaveTextContent("Customer is inactive");
    expect(screen.getByTestId("error-birth_year")).toHaveTextContent("1900");
    expect(screen.getByTestId("error-sex")).toBeInTheDocument();
    expect(screen.getByTestId("error-breed")).toBeInTheDocument();
    expect(screen.queryByTestId("form-error")).toBeNull();
    expect(owner().getByRole("combobox")).toHaveValue("Anna Andersson"); // the draft survives
    expect(screen.getByLabelText("Birth year")).toHaveValue("1850");
  });

  it("a foreign customer id and a random one look exactly the same to the user", async () => {
    const same = { loc: ["body", "owner_customer_id"], msg: "Customer not found", type: "reference.not_found" };
    backend(() => fail(422, { detail: [same] }));
    const first = mount(A);
    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Anna/);
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));
    const asForeign = (await screen.findByTestId("error-owner_customer_id")).textContent;
    first.unmount();

    backend(() => fail(422, { detail: [same] }));
    mount(A);
    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Umeå/);
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));
    const asRandom = (await screen.findByTestId("error-owner_customer_id")).textContent;

    expect(asForeign).toBe("Customer not found");
    expect(asRandom).toBe(asForeign);
  });

  it.each([
    [fail<Horse>(403, { detail: "Your role does not allow this." }), "Your role does not allow this."],
    [fail<Horse>(500, { detail: "Traceback" }), "could not complete the request"],
    [{ ok: false, error: networkError() } as ApiResult<Horse>, "Could not reach the server"],
  ])("shows %# as a form-level message and can be retried", async (result, text) => {
    backend(() => result);
    mount(A);
    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Anna/);

    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    expect(await screen.findByTestId("form-error")).toHaveTextContent(text);
    expect(owner().getByRole("combobox")).toHaveValue("Anna Andersson");
  });
});

describe("editing a horse", () => {
  it("shows the saved owner and stable, and an inactive one is displayed, not dropped", () => {
    backend(() => ok(horse()));
    mount(A, horse({ owner: { id: OLD.id, name: OLD.name, active: false }, owner_customer_id: OLD.id }));

    expect(owner().getByRole("combobox")).toHaveValue("Old Owner (inactive)");
    expect(stable().getByRole("combobox")).toHaveValue("Umeå HK");
    expect(screen.getByLabelText("Birth year")).toHaveValue("2012");
    expect(screen.getByLabelText("Sex")).toHaveValue("gelding");
  });

  it("editing another field never sends the owner or stable, so an inactive owner does not block the save", async () => {
    const existing = horse({ owner: { id: OLD.id, name: OLD.name, active: false }, owner_customer_id: OLD.id });
    backend(() => ok({ ...existing, breed: "Fjord" }));
    mount(A, existing);

    await userEvent.clear(screen.getByLabelText("Breed"));
    await userEvent.type(screen.getByLabelText("Breed"), "Fjord");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(lastWrite()).toEqual([A, `/horses/${existing.id}`, { method: "PATCH", body: { breed: "Fjord" } }]);
    expect(await screen.findByTestId("saved")).toBeInTheDocument();
    expect(owner().getByRole("combobox")).toHaveValue("Old Owner (inactive)"); // still there after the save
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("changing the owner sends the new id; changing the stable to none sends null", async () => {
    const existing = horse();
    backend(() => ok(existing));
    mount(A, existing);

    await choose(owner(), /Umeå/);
    await userEvent.click(stable().getByRole("button", { name: "Clear Stable" }));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(lastWrite()[2]).toEqual({ method: "PATCH", body: { owner_customer_id: UMEA.id, stable_customer_id: null } });
  });

  it("a deliberate change to a customer the backend refuses (inactive since the list was loaded) shows on the Owner control, and nothing else changes", async () => {
    const existing = horse();
    backend(() => referenceError("owner_customer_id", "Customer is inactive", "reference.inactive"));
    mount(A, existing);
    await choose(owner(), /Umeå/);

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByTestId("error-owner_customer_id")).toHaveTextContent("Customer is inactive");
    expect(screen.queryByTestId("error-stable_customer_id")).toBeNull();
    expect(owner().getByRole("combobox")).toHaveValue("Umeå HK"); // the draft is kept for the user to change
    expect(screen.queryByTestId("saved")).toBeNull();
  });

  it("clearing the birth year, sex and breed sends null for each", async () => {
    const existing = horse();
    backend(() => ok(existing));
    mount(A, existing);

    await userEvent.clear(screen.getByLabelText("Birth year"));
    await userEvent.selectOptions(screen.getByLabelText("Sex"), "");
    await userEvent.clear(screen.getByLabelText("Breed"));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(lastWrite()[2]).toEqual({ method: "PATCH", body: { birth_year: null, sex: null, breed: null } });
  });

  it("makes no request when nothing changed", async () => {
    backend(() => ok(horse()));
    mount(A, horse());
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(writes()).toHaveLength(0);
    expect(screen.getByTestId("unchanged")).toBeInTheDocument();
  });

  it("a horse that vanished (or was never in this organization) says so in the same words either way", async () => {
    backend(() => fail(404, { detail: "Not found" }));
    mount(A, horse());
    await userEvent.type(screen.getByLabelText("Breed"), "x");

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByTestId("form-error")).toHaveTextContent("This record no longer exists, or you do not have access to it.");
  });

  it("deactivates and reactivates with the shared toggle, leaving unsaved edits alone", async () => {
    const existing = horse();
    backend((_path, body) => ok({ ...existing, active: (body as { active: boolean }).active }));
    mount(A, existing);
    await userEvent.type(screen.getByLabelText("Breed"), " mix");

    await userEvent.click(screen.getByRole("button", { name: "Deactivate horse" }));
    expect(lastWrite()).toEqual([A, `/horses/${existing.id}`, { method: "PATCH", body: { active: false } }]);
    expect(await screen.findByRole("button", { name: "Reactivate horse" })).toBeInTheDocument();
    expect(screen.getByLabelText("Breed")).toHaveValue("Icelandic mix");

    await userEvent.click(screen.getByRole("button", { name: "Reactivate horse" }));
    expect(await screen.findByRole("button", { name: "Deactivate horse" })).toBeInTheDocument();
  });
});

describe("organization scope", () => {
  it("a draft with chosen customers is gone, picker state included, when the organization changes", async () => {
    backend(() => ok(horse(), 201));
    const { rerender } = mount(A);
    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Typed in A");
    await choose(owner(), /Anna/);
    expect(owner().getByRole("combobox")).toHaveValue("Anna Andersson");

    rerender(
      <OrgScope orgId={B}>
        <HorseForm />
      </OrgScope>,
    );

    expect(screen.getByLabelText("Name", { exact: true })).toHaveValue("");
    expect(owner().getByRole("combobox")).toHaveValue("");
    expect(document.querySelector<HTMLInputElement>('input[name="owner_customer_id"]')!.value).toBe("");
  });

  it("searches and saves for the organization it is shown for", async () => {
    backend(() => ok(horse(), 201));
    const { rerender } = mount(A);
    rerender(
      <OrgScope orgId={B}>
        <HorseForm />
      </OrgScope>,
    );

    await userEvent.type(screen.getByLabelText("Name", { exact: true }), "Kalle");
    await choose(owner(), /Anna/);
    await userEvent.click(screen.getByRole("button", { name: "Create horse" }));

    await waitFor(() => expect(router.push).toHaveBeenCalled());
    expect(mocked.mock.calls.every(([orgId]) => orgId === B)).toBe(true);
    expect(router.push).toHaveBeenCalledWith(expect.stringContaining(`/o/${B}/`));
  });
});
