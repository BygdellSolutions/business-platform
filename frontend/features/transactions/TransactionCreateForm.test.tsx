import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { TransactionCreateForm } from "@/features/transactions/TransactionCreateForm";
import { ANNA_ID, ORG_A, ORG_B, UMEA_ID, fail, installBackend, invalid, ok, resetServer, router, searches, tx, writes } from "@/features/transactions/testing";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(tx());
  installBackend(() => ok(tx({ id: "new-id" }), 201));
  vi.useFakeTimers({ toFake: ["Date"], now: new Date(2026, 9, 3, 23, 30) }); // 3 Oct 2026, 23:30 local time
});
afterEach(() => {
  vi.useRealTimers();
});

const picker = () => within(screen.getByTestId("picker-billing_customer_id"));
const dateInput = () => screen.getByLabelText("Date") as HTMLInputElement;

function mount(orgId = ORG_A) {
  return render(
    <OrgScope orgId={orgId}>
      <TransactionCreateForm />
    </OrgScope>,
  );
}

async function chooseCustomer(name: RegExp) {
  await userEvent.click(picker().getByRole("combobox"));
  await userEvent.click(await picker().findByRole("option", { name }));
}

describe("creating a transaction", () => {
  it("is prefilled with the browser's local calendar date, as a plain YYYY-MM-DD", () => {
    mount();
    expect(dateInput()).toHaveValue("2026-10-03");
  });

  it("creates the draft with the customer's id and the date only, then opens it and refreshes the router cache", async () => {
    mount();
    await chooseCustomer(/Anna/);

    await userEvent.click(screen.getByTestId("submit"));

    expect(writes()).toEqual([{ method: "POST", path: "/transactions", body: { billing_customer_id: ANNA_ID, transaction_date: "2026-10-03" }, ifMatch: undefined, orgId: ORG_A }]);
    expect(Object.keys(writes()[0].body as object).sort()).toEqual(["billing_customer_id", "transaction_date"]); // no lines, no status, no organization
    expect(router.push).toHaveBeenCalledWith(`/o/${ORG_A}/transactions/new-id?created=1`);
    expect(router.refresh).toHaveBeenCalledTimes(1); // so Back to an earlier list does not show a cached copy
  });

  it("sends the date the user picked", async () => {
    mount();
    await chooseCustomer(/Umeå/);
    fireEvent.change(dateInput(), { target: { value: "2026-12-24" } });

    await userEvent.click(screen.getByTestId("submit"));

    expect(writes()[0].body).toEqual({ billing_customer_id: UMEA_ID, transaction_date: "2026-12-24" });
  });

  it("a cleared date is left out, and the backend picks the day", async () => {
    mount();
    await chooseCustomer(/Anna/);
    fireEvent.change(dateInput(), { target: { value: "" } });

    await userEvent.click(screen.getByTestId("submit"));

    expect(writes()[0].body).toEqual({ billing_customer_id: ANNA_ID });
  });

  it("only active customers can be chosen to bill", async () => {
    mount();
    await userEvent.click(picker().getByRole("combobox"));
    await picker().findAllByRole("option");

    expect(picker().queryByRole("option", { name: /Old Customer/ })).toBeNull();
    for (const path of searches()) expect(new URL(path, "http://x").searchParams.get("active")).toBe("true");
  });

  it("with no customer chosen the field is left out and the backend's answer shows on the picker", async () => {
    installBackend(() => invalid(["billing_customer_id", "Field required"]));
    mount();

    await userEvent.click(screen.getByTestId("submit"));

    expect("billing_customer_id" in (writes()[0].body as object)).toBe(false);
    expect(await screen.findByTestId("error-billing_customer_id")).toHaveTextContent("Field required");
    expect(picker().getByRole("combobox")).toHaveAttribute("aria-invalid", "true");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("a customer the backend refuses (inactive since the list loaded) shows on the picker and keeps the form", async () => {
    installBackend(() => invalid(["billing_customer_id", "Customer is inactive"]));
    mount();
    await chooseCustomer(/Anna/);
    fireEvent.change(dateInput(), { target: { value: "2026-11-11" } });

    await userEvent.click(screen.getByTestId("submit"));

    expect(await screen.findByTestId("error-billing_customer_id")).toHaveTextContent("Customer is inactive");
    expect(dateInput()).toHaveValue("2026-11-11");
    expect(picker().getByRole("combobox")).toHaveValue("Anna Andersson");
  });

  it("maps a date the backend refuses onto the date control", async () => {
    installBackend(() => invalid(["transaction_date", "Input should be a valid date"]));
    mount();
    await chooseCustomer(/Anna/);
    await userEvent.click(screen.getByTestId("submit"));
    expect(await screen.findByTestId("error-transaction_date")).toHaveTextContent("valid date");
  });

  it.each([
    [fail(403, { detail: "Your role in this organization does not allow this." }), "Your role in this organization does not allow this."],
    [fail(500, { detail: "Traceback" }), "could not complete the request"],
  ])("shows %# as a form-level message and can be retried", async (result, text) => {
    installBackend(() => result);
    mount();
    await chooseCustomer(/Anna/);
    await userEvent.click(screen.getByTestId("submit"));
    expect(await screen.findByTestId("form-error")).toHaveTextContent(text);
    expect(picker().getByRole("combobox")).toHaveValue("Anna Andersson");
  });

  it("creates once however often the button is pressed", async () => {
    let finish!: (value: ReturnType<typeof ok>) => void;
    installBackend(() => new Promise((resolve) => (finish = resolve)));
    mount();
    await chooseCustomer(/Anna/);

    await userEvent.click(screen.getByTestId("submit"));
    await userEvent.click(screen.getByRole("button", { name: "Creating…" }));

    expect(writes()).toHaveLength(1);
    finish(ok(tx({ id: "new-id" }), 201));
  });

  it("does not carry a chosen customer or a typed date to another organization", async () => {
    const { rerender } = mount(ORG_A);
    await chooseCustomer(/Anna/);
    fireEvent.change(dateInput(), { target: { value: "2030-01-01" } });

    rerender(
      <OrgScope orgId={ORG_B}>
        <TransactionCreateForm />
      </OrgScope>,
    );

    expect(picker().getByRole("combobox")).toHaveValue("");
    expect(dateInput()).toHaveValue("2026-10-03"); // today again, not the typed 2030 date
    await userEvent.click(screen.getByTestId("submit"));
    expect(writes()[0].orgId).toBe(ORG_B);
  });
});
