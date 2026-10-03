import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ANNA_ID,
  Harness,
  ORG_A,
  TX_ID,
  UMEA_ID,
  fail,
  installBackend,
  invalid,
  line,
  ok,
  resetServer,
  router,
  searches,
  second,
  server,
  stale,
  tx,
  writes,
} from "@/features/transactions/testing";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(tx());
  installBackend(() => ok(tx()));
});

const picker = () => within(screen.getByTestId("picker-billing_customer_id"));
const dateInput = () => screen.getByLabelText("Date") as HTMLInputElement;

async function chooseCustomer(name: RegExp) {
  await userEvent.click(picker().getByRole("combobox"));
  await userEvent.click(await picker().findByRole("option", { name }));
}

function setDate(value: string) {
  fireEvent.change(dateInput(), { target: { value } });
}

describe("editing the header", () => {
  it("opens with the current customer and date", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));

    expect(picker().getByRole("combobox")).toHaveValue("Anna Andersson");
    expect(dateInput()).toHaveValue("2026-10-01");
  });

  it("sends only the date when only the date changed, with the header's version, as plain YYYY-MM-DD", async () => {
    render(<Harness initial={tx({ header_version: 3 })} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    setDate("2026-11-02");

    await userEvent.click(screen.getByTestId("save-header"));

    expect(writes()).toEqual([{ method: "PATCH", path: `/transactions/${TX_ID}`, body: { transaction_date: "2026-11-02" }, ifMatch: 3, orgId: ORG_A }]);
    expect(router.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByTestId("save-header")).toBeNull());
  });

  it("uses the HEADER version, not the transaction's: a change to a line elsewhere is not a header conflict", async () => {
    render(<Harness initial={tx({ version: 4, header_version: 2 })} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    setDate("2026-11-02");

    server.tx = tx({ version: 9, header_version: 2, lines: [line({ version: 5 }), second()] }); // lines changed, header did not
    await act(async () => router.refresh());

    expect(screen.queryByTestId("header-conflict")).toBeNull();
    await userEvent.click(screen.getByTestId("save-header"));
    expect(writes()[0].ifMatch).toBe(2);
  });

  it("changing the customer sends the customer's id, not its name", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));

    await chooseCustomer(/Umeå HK/);
    await userEvent.click(screen.getByTestId("save-header"));

    expect(writes()[0].body).toEqual({ billing_customer_id: UMEA_ID });
  });

  it("offers only active customers to bill", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    await userEvent.click(picker().getByRole("combobox"));
    await picker().findAllByRole("option");

    expect(picker().queryByRole("option", { name: /Old Customer/ })).toBeNull();
    const customerSearches = searches().filter((path) => path.startsWith("/customers?"));
    expect(customerSearches.length).toBeGreaterThan(0);
    for (const path of customerSearches) expect(new URL(path, "http://x").searchParams.get("active")).toBe("true");
  });

  it("a customer that was deactivated since is displayed, and editing the date does not resend or clear it", async () => {
    const inactive = tx({ billing_customer: { id: ANNA_ID, name: "Anna Andersson", active: false } });
    render(<Harness initial={inactive} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    expect(picker().getByRole("combobox")).toHaveValue("Anna Andersson (inactive)");

    setDate("2026-12-24");
    await userEvent.click(screen.getByTestId("save-header"));

    expect(writes()[0].body).toEqual({ transaction_date: "2026-12-24" }); // the unchanged customer is not sent
  });

  it("changing to a customer that became inactive is refused by the backend, on the customer control", async () => {
    installBackend(() => invalid(["billing_customer_id", "Customer is inactive"]));
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    await chooseCustomer(/Umeå HK/);

    await userEvent.click(screen.getByTestId("save-header"));

    expect(await screen.findByTestId("error-billing_customer_id")).toHaveTextContent("Customer is inactive");
    expect(screen.queryByTestId("error-transaction_date")).toBeNull();
    expect(picker().getByRole("combobox")).toHaveValue("Umeå HK"); // the draft stays for the user to change
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("maps a date the backend refuses onto the date control", async () => {
    installBackend(() => invalid(["transaction_date", "Input should be a valid date"]));
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    setDate("2026-11-02"); // a real date; the mocked backend refuses it

    await userEvent.click(screen.getByTestId("save-header"));

    expect(await screen.findByTestId("error-transaction_date")).toHaveTextContent("valid date");
    expect(screen.queryByTestId("error-billing_customer_id")).toBeNull();
  });

  it("a cleared date is stopped locally (it is not a date), and nothing is sent", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    setDate("");

    await userEvent.click(screen.getByTestId("save-header"));

    expect(screen.getByTestId("error-transaction_date")).toHaveTextContent("Enter a date");
    expect(writes()).toHaveLength(0);
  });

  it("makes no request when nothing changed", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    await userEvent.click(screen.getByTestId("save-header"));
    expect(writes()).toHaveLength(0);
    expect(screen.queryByTestId("save-header")).toBeNull();
  });

  it("Cancel closes without sending anything", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    setDate("2030-01-01");
    await userEvent.click(screen.getByTestId("cancel-header"));
    expect(writes()).toHaveLength(0);
    expect(screen.getByTestId("header-date")).toHaveTextContent("2026-10-01");
  });
});

describe("a header that changed elsewhere", () => {
  it("when FastAPI refuses the version, the draft is kept and saving is off; the latest is loaded around it", async () => {
    installBackend(() => stale(5));
    server.tx = tx({ header_version: 5, version: 8, transaction_date: "2026-09-09" });
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    await chooseCustomer(/Umeå HK/);
    setDate("2031-05-05");

    await userEvent.click(screen.getByTestId("save-header"));

    expect(await screen.findByTestId("header-conflict")).toHaveTextContent("The header was changed elsewhere. Your edits were not saved.");
    expect(picker().getByRole("combobox")).toHaveValue("Umeå HK");
    expect(dateInput()).toHaveValue("2031-05-05");
    expect(screen.getByTestId("save-header")).toBeDisabled();
    expect(router.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId("header-conflict")).toHaveTextContent("2026-09-09")); // what is there now
    expect(dateInput()).toHaveValue("2031-05-05"); // the draft is still the user's
    expect(writes()).toHaveLength(1);
  });

  it("the user chooses: discarding closes the editor and loads the latest", async () => {
    installBackend(() => stale(5));
    server.tx = tx({ header_version: 5, version: 8, transaction_date: "2026-09-09" });
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    setDate("2031-05-05");
    await userEvent.click(screen.getByTestId("save-header"));
    await screen.findByTestId("header-conflict");
    router.refresh.mockClear();

    await userEvent.click(screen.getByTestId("discard-header"));

    expect(router.refresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("header-conflict")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("header-date")).toHaveTextContent("2026-09-09"));
  });

  it("when the server's header has already moved on, the conflict shows at once with what is there now, and the draft stays", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    setDate("2031-05-05");

    server.tx = tx({ header_version: 3, version: 5, transaction_date: "2026-09-09", billing_customer: { id: UMEA_ID, name: "Umeå HK", active: true }, billing_customer_id: UMEA_ID });
    await act(async () => router.refresh());

    const conflict = screen.getByTestId("header-conflict");
    expect(conflict).toHaveTextContent("Umeå HK");
    expect(conflict).toHaveTextContent("2026-09-09");
    expect(dateInput()).toHaveValue("2031-05-05");
    expect(screen.getByTestId("save-header")).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });

  it("a header edit on a transaction completed elsewhere explains that it cannot be saved, and shows the read-only state", async () => {
    installBackend(() => fail(409, { detail: "A completed transaction cannot be changed; reopen it first" }));
    server.tx = tx({ status: "completed", version: 6 });
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    setDate("2031-05-05");

    await userEvent.click(screen.getByTestId("save-header"));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("no longer a draft, so your changes could not be saved");
    await waitFor(() => expect(screen.queryByTestId("save-header")).toBeNull());
    expect(screen.queryByTestId("edit-header")).toBeNull();
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });
});
