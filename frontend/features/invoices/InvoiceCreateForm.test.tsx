import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { InvoiceCreateForm } from "@/features/invoices/InvoiceCreateForm";
import { INVOICE_ID, ORG_A, ORG_B, calls, conflict, deferred, eligible, fail, installBackend, invoice, network, ok, resetServer, router, writes } from "@/features/invoices/testing";
import type { Invoiceable } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const ANNA = "77777777-7777-4777-8777-777777777777";
const R1 = "10000000-0000-4000-8000-000000000001";
const R2 = "10000000-0000-4000-8000-000000000002";
const R3 = "10000000-0000-4000-8000-000000000003";
const R4 = "10000000-0000-4000-8000-000000000004";

const ROWS: Invoiceable[] = [
  eligible(R1, { transaction_date: "2026-10-05" }),
  eligible(R2, { transaction_date: "2026-10-04" }),
  eligible(R3, { transaction_date: "2026-10-03", billing_customer_id: ANNA, billing_customer: { id: ANNA, name: "Anna Andersson", active: true } }),
  eligible(R4, { transaction_date: "2026-10-02", currency: "EUR" }),
];

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(invoice());
  installBackend(() => ok(null));
});

function mount(orgId = ORG_A, props: Partial<Parameters<typeof InvoiceCreateForm>[0]> = {}) {
  return render(
    <OrgScope orgId={orgId}>
      <InvoiceCreateForm rows={ROWS} hasNext={false} base={`/o/${orgId}/invoices/new`} page={1} customerFilter={null} {...props} />
    </OrgScope>,
  );
}

const boxes = () => screen.getAllByTestId("select-transaction") as HTMLInputElement[];
const rowOf = (id: string) => screen.getAllByTestId("eligible-row")[ROWS.findIndex((row) => row.id === id)];
const pick = async (id: string) => userEvent.click(within(rowOf(id)).getByTestId("select-transaction"));
const text = (testId: string) => screen.getByTestId(testId).textContent;

describe("the list", () => {
  it("shows what the server listed: date, billing customer, currency, lines and the server's totals", () => {
    mount();
    expect(screen.getAllByTestId("eligible-row")).toHaveLength(4);
    expect(within(rowOf(R1)).getByTestId("eligible-customer")).toHaveTextContent("Umeå HK");
    expect(within(rowOf(R4)).getByTestId("eligible-currency")).toHaveTextContent("EUR");
    expect(rowOf(R1)).toHaveTextContent("850.00");
    expect(rowOf(R1)).toHaveTextContent("212.50");
    expect(rowOf(R1)).toHaveTextContent("1062.50");
  });

  it("says so when nothing is waiting to be invoiced", () => {
    mount(ORG_A, { rows: [] });
    expect(screen.getByTestId("empty")).toHaveTextContent("No completed orders");
  });

  it("starts with an empty selection and nothing to submit", () => {
    mount();
    expect(screen.getByTestId("selection-empty")).toBeInTheDocument();
    expect(screen.getByTestId("submit")).toBeDisabled();
  });
});

describe("the selection can only hold compatible transactions", () => {
  it("the first selection fixes the customer and the currency, and shows them", async () => {
    mount();
    await pick(R1);
    expect(text("selection-customer")).toBe("Umeå HK");
    expect(text("selection-currency")).toBe("SEK");
    expect(text("selection-count")).toBe("1");
  });

  it("then only the same customer AND currency can be added; the others are disabled and say why", async () => {
    mount();
    await pick(R1);

    expect(within(rowOf(R2)).getByTestId("select-transaction")).toBeEnabled();
    expect(within(rowOf(R3)).getByTestId("select-transaction")).toBeDisabled();
    expect(within(rowOf(R3)).getByTestId("incompatible-reason")).toHaveTextContent("Another billing customer");
    expect(within(rowOf(R4)).getByTestId("select-transaction")).toBeDisabled();
    expect(within(rowOf(R4)).getByTestId("incompatible-reason")).toHaveTextContent("Another currency");
    expect(rowOf(R3)).toHaveAttribute("data-compat", "different_customer");
    expect(rowOf(R4)).toHaveAttribute("data-compat", "different_currency");
  });

  it("an incompatible transaction cannot be added, even by clicking its disabled box", async () => {
    mount();
    await pick(R1);
    await pick(R3);
    await pick(R4);
    expect(text("selection-count")).toBe("1");
    expect(boxes().filter((box) => box.checked)).toHaveLength(1);
  });

  it("compatible ones can be added and removed; emptying the selection frees every row again", async () => {
    mount();
    await pick(R1);
    await pick(R2);
    expect(text("selection-count")).toBe("2");

    await userEvent.click(within(screen.getAllByTestId("selected-row")[0]).getByTestId("unselect"));
    expect(text("selection-count")).toBe("1");
    expect(text("selection-customer")).toBe("Umeå HK");

    await userEvent.click(screen.getByTestId("clear-selection"));
    expect(screen.getByTestId("selection-empty")).toBeInTheDocument();
    for (const box of boxes()) expect(box).toBeEnabled();
  });

  it("the customer and currency are decided by the FIRST selected one: removing it lets the rest decide", async () => {
    mount();
    await pick(R3);
    expect(within(rowOf(R1)).getByTestId("select-transaction")).toBeDisabled();
    await userEvent.click(within(screen.getByTestId("selected-row")).getByTestId("unselect"));
    await pick(R4);
    expect(text("selection-currency")).toBe("EUR");
    expect(within(rowOf(R1)).getByTestId("select-transaction")).toBeDisabled();
  });

  it("shows no combined amount: only each transaction's own server figures", async () => {
    mount();
    await pick(R1);
    await pick(R2);
    expect(screen.queryByText(/combined|grand total|sum/i)).toBeNull();
    const selected = screen.getAllByTestId("selected-row");
    expect(selected).toHaveLength(2);
    for (const row of selected) expect(row).toHaveTextContent("net 850.00, VAT 212.50, gross 1062.50");
    // Two equal transactions would give 1700.00 / 425.00 / 2125.00 if anything added them up.
    expect(screen.getByTestId("selection").textContent).not.toMatch(/1700\.00|425\.00|2125\.00/);
  });
});

describe("creating the draft", () => {
  it("sends the chosen ids and only the approved header fields, never a customer, a currency or an organization", async () => {
    installBackend(({ method }) => (method === "POST" ? ok(invoice({ id: INVOICE_ID }), 201) : ok(null)));
    mount();
    await pick(R1);
    await pick(R2);
    fireEvent.change(screen.getByLabelText("Invoice date"), { target: { value: "2026-10-06" } });
    fireEvent.change(screen.getByLabelText("Due date"), { target: { value: "2026-11-05" } });
    await userEvent.type(screen.getByLabelText("Description"), "October");
    await userEvent.click(screen.getByTestId("submit"));

    expect(writes()).toHaveLength(1);
    const [call] = writes();
    expect(call).toMatchObject({ method: "POST", path: "/invoices", orgId: ORG_A });
    expect(call.body).toEqual({ transaction_ids: [R1, R2], invoice_date: "2026-10-06", due_date: "2026-11-05", description: "October" });
    expect(Object.keys(call.body as object).sort()).toEqual(["description", "due_date", "invoice_date", "transaction_ids"]);
    const wire = JSON.stringify(call.body);
    for (const forbidden of ["customer", "currency", "organization", "SEK", "net_amount", "billing"]) expect(wire).not.toContain(forbidden);
  });

  it("leaves blank header fields out (the backend defaults the date)", async () => {
    installBackend(({ method }) => (method === "POST" ? ok(invoice(), 201) : ok(null)));
    mount();
    await pick(R2);
    await userEvent.click(screen.getByTestId("submit"));
    expect(writes()[0].body).toEqual({ transaction_ids: [R2] });
  });

  it("can only send ids it was given by the server's list: there is no control to type or paste an id", async () => {
    installBackend(({ method }) => (method === "POST" ? ok(invoice(), 201) : ok(null)));
    mount();
    expect(screen.getAllByRole("textbox")).toHaveLength(1); // only the description
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    const sent = (writes()[0].body as { transaction_ids: string[] }).transaction_ids;
    expect(sent.every((id) => ROWS.some((row) => row.id === id))).toBe(true);
  });

  it("goes to the new draft, in this organization", async () => {
    installBackend(({ method }) => (method === "POST" ? ok(invoice({ id: INVOICE_ID }), 201) : ok(null)));
    mount(ORG_B);
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    expect(router.push).toHaveBeenCalledWith(`/o/${ORG_B}/invoices/${INVOICE_ID}?created=1`);
    expect(writes()[0].orgId).toBe(ORG_B);
  });

  it("sends once however often it is submitted in the same instant", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "POST" ? answer.promise : ok(null)));
    mount();
    await pick(R1);
    const form = screen.getByRole("form", { name: "New invoice" });
    await act(async () => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    expect(writes()).toHaveLength(1);
    await act(async () => answer.resolve(ok(invoice(), 201)));
  });
});

describe("a transaction stops being invoiceable between listing and creating", () => {
  it("shows the backend's structured conflict, forgets the transactions it names, and re-reads eligibility", async () => {
    let attempt = 0;
    installBackend(({ method }) => {
      if (method !== "POST") return ok(null);
      attempt += 1;
      return attempt === 1 ? conflict("already_invoiced", "A transaction is already on a draft or issued invoice", { transaction_ids: [R1] }) : ok(invoice({ id: INVOICE_ID }), 201);
    });
    mount();
    await pick(R1);
    await pick(R2);
    await userEvent.click(screen.getByTestId("submit"));

    const notice = await screen.findByTestId("eligibility-conflict");
    expect(notice).toHaveTextContent("already on a draft or issued invoice");
    expect(within(notice).getByTestId("conflict-transactions")).toHaveTextContent("2026-10-05 · Umeå HK");
    expect(text("selection-count")).toBe("1"); // R1 was dropped, R2 stays
    expect(router.refresh).toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("submit"));
    expect(writes().map((call) => (call.body as { transaction_ids: string[] }).transaction_ids)).toEqual([[R1, R2], [R2]]);
    expect(router.push).toHaveBeenCalledWith(`/o/${ORG_A}/invoices/${INVOICE_ID}?created=1`);
  });

  it.each([
    ["transactions_not_completed", "Only completed orders can be invoiced"],
    ["currency_missing", "A transaction without a currency cannot be invoiced"],
  ])("also handles %s the same way", async (code, message) => {
    installBackend(({ method }) => (method === "POST" ? conflict(code, message, { transaction_ids: [R2] }) : ok(null)));
    mount();
    await pick(R1);
    await pick(R2);
    await userEvent.click(screen.getByTestId("submit"));
    expect(await screen.findByTestId("eligibility-conflict")).toHaveTextContent(message);
    expect(text("selection-count")).toBe("1");
  });

  it("a conflict that names nothing keeps the selection (the user chooses what to change) and still refreshes", async () => {
    installBackend(({ method }) => (method === "POST" ? conflict("mixed_currencies", "All transactions on an invoice must be in the same currency") : ok(null)));
    mount();
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    expect(await screen.findByTestId("eligibility-conflict")).toBeInTheDocument();
    expect(text("selection-count")).toBe("1");
    expect(router.refresh).toHaveBeenCalled();
  });

  it("ids the backend cannot find are reported without revealing why, and eligibility is re-read", async () => {
    installBackend(({ method }) => (method === "POST" ? fail(422, { detail: [{ loc: ["body", "transaction_ids"], msg: "One or more transactions were not found", type: "reference.not_found" }] }) : ok(null)));
    mount();
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    expect(await screen.findByTestId("create-problem")).toHaveTextContent("could not be found");
    expect(router.refresh).toHaveBeenCalled();
  });

  it("an unknown outcome is not reported as a failure and not retried: the user is told to check the list", async () => {
    installBackend(({ method }) => (method === "POST" ? network() : ok(null)));
    mount();
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    expect(await screen.findByTestId("create-problem")).toHaveTextContent("could not confirm");
    expect(writes()).toHaveLength(1);
    expect(router.push).not.toHaveBeenCalled();
  });

  it("a role the backend refuses gets the backend's message", async () => {
    installBackend(({ method }) => (method === "POST" ? fail(403, { detail: "Your role in this organization does not allow this action" }) : ok(null)));
    mount();
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    expect(await screen.findByTestId("form-error")).toHaveTextContent("does not allow this action");
  });
});

describe("late responses and organization switching", () => {
  it("an answer that arrives after the screen was left does not navigate", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "POST" ? answer.promise : ok(null)));
    const { unmount } = mount();
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    unmount();
    await act(async () => answer.resolve(ok(invoice({ id: INVOICE_ID }), 201)));
    expect(router.push).not.toHaveBeenCalled();
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("a conflict that arrives after the screen was left changes nothing", async () => {
    const answer = deferred<ReturnType<typeof conflict>>();
    installBackend(({ method }) => (method === "POST" ? answer.promise : ok(null)));
    const { unmount } = mount();
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    unmount();
    await act(async () => answer.resolve(conflict("already_invoiced", "taken", { transaction_ids: [R1] })));
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("switching organization clears the selection and the header draft, and a late answer for the old one is ignored", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "POST" ? answer.promise : ok(null)));
    const view = render(
      <OrgScope orgId={ORG_A}>
        <InvoiceCreateForm rows={ROWS} hasNext={false} base="/x" page={1} customerFilter={null} />
      </OrgScope>,
    );
    await pick(R1);
    await userEvent.type(screen.getByLabelText("Description"), "A's draft text");
    await userEvent.click(screen.getByTestId("submit"));

    view.rerender(
      <OrgScope orgId={ORG_B}>
        <InvoiceCreateForm rows={[eligible("20000000-0000-4000-8000-000000000001", { transaction_date: "2026-11-01" })]} hasNext={false} base="/y" page={1} customerFilter={null} />
      </OrgScope>,
    );
    await act(async () => answer.resolve(ok(invoice({ id: INVOICE_ID }), 201)));

    expect(screen.getByTestId("selection-empty")).toBeInTheDocument();
    expect(screen.getByLabelText("Description")).toHaveValue("");
    expect(router.push).not.toHaveBeenCalled();
    expect(screen.getAllByTestId("eligible-row")).toHaveLength(1);
  });

  it("requests are made for the organization of the screen", async () => {
    installBackend(({ method }) => (method === "POST" ? ok(invoice(), 201) : ok(null)));
    mount(ORG_B);
    await pick(R1);
    await userEvent.click(screen.getByTestId("submit"));
    expect(calls().every((call) => call.orgId === ORG_B)).toBe(true);
  });
});

describe("refreshing eligibility", () => {
  it("re-reads when the tab comes back, and the selection survives it", async () => {
    mount();
    await pick(R1);
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(router.refresh).toHaveBeenCalledTimes(1);
    expect(text("selection-count")).toBe("1");
  });
});

describe("paging and the customer filter", () => {
  it("offers 'only this customer' once something is selected, as a link to the backend's own filter", async () => {
    mount();
    await pick(R1);
    expect(screen.getByTestId("only-this-customer")).toHaveAttribute("href", expect.stringContaining("customer_id=44444444-4444-4444-8444-444444444444"));
  });

  it("with a filter active it offers to show all customers instead, and paging keeps the filter", () => {
    mount(ORG_A, { customerFilter: "44444444-4444-4444-8444-444444444444", hasNext: true, page: 2 });
    expect(screen.getByTestId("clear-customer-filter")).toBeInTheDocument();
    expect(screen.getByTestId("next-page")).toHaveAttribute("href", expect.stringMatching(/customer_id=4444.*page=3|page=3.*customer_id=4444/));
    expect(screen.getByTestId("prev-page")).toHaveAttribute("href", expect.stringContaining("customer_id=4444"));
  });

  it("does not filter or sort anything in the browser: rows appear in the order the server sent them", () => {
    mount();
    expect(screen.getAllByTestId("eligible-row").map((row) => row.textContent?.slice(0, 10))).toEqual(["2026-10-05", "2026-10-04", "2026-10-03", "2026-10-02"]);
  });
});

describe("a reader of invoices", () => {
  it("sees what is waiting but gets no selection, no form and nothing to submit", () => {
    mount(ORG_A, { canMutate: false });
    expect(screen.getAllByTestId("eligible-row")).toHaveLength(4);
    expect(screen.queryByTestId("select-transaction")).toBeNull();
    expect(screen.queryByTestId("selection")).toBeNull();
    expect(screen.queryByRole("form", { name: "New invoice" })).toBeNull();
    expect(screen.queryByTestId("submit")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
