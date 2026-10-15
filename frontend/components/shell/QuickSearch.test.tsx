import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QuickSearch } from "@/components/shell/QuickSearch";
import { normalizeError, type ApiResult } from "@/lib/api/errors";

vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const mocked = vi.mocked(apiFetch);
const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, status: 200, data });
const PAGES = [
  { label: "Customers", path: "/customers" },
  { label: "Orders", path: "/transactions" },
  { label: "Invoices", path: "/invoices" },
];

function backend(rows: Record<string, unknown[]>) {
  mocked.mockImplementation((async (_org: string, path: string) => {
    const key = path.split("?")[0];
    if (key in rows) return ok(rows[key]);
    return { ok: false, error: normalizeError(403, { detail: "no" }) };
  }) as never);
}

beforeEach(() => {
  mocked.mockReset();
  backend({});
});

const type = async (text: string) => userEvent.type(screen.getByTestId("quick-search-input"), text);

describe("the quick search", () => {
  it("offers the pages of the menu that match, without asking the backend", async () => {
    render(<QuickSearch orgId={ORG} pages={PAGES} />);
    await type("cust");
    expect(screen.getByRole("option", { name: "Customers" })).toHaveAttribute("href", `/o/${ORG}/customers`);
    expect(screen.queryByRole("option", { name: "Orders" })).toBeNull();
  });

  it("finds customers, horses, products, suppliers and invoices from the lists that already exist, grouped", async () => {
    backend({
      "/customers": [{ id: "c1", name: "Anna Andersson", number: 7 }],
      "/horses": [{ id: "h1", name: "Anna's Kalle", number: 3 }],
      "/items": [],
      "/suppliers": [],
      "/invoices": [{ id: "i1", number_text: "1001", customer_name: "Anna Andersson" }],
    });
    render(<QuickSearch orgId={ORG} pages={PAGES} />);
    await type("anna");
    const hits = await screen.findAllByTestId("quick-search-hit");
    expect(hits.map((hit) => [hit.getAttribute("href"), hit.textContent])).toEqual([
      [`/o/${ORG}/customers/c1`, "Anna AnderssonCustomer 7"],
      [`/o/${ORG}/horses/h1`, "Anna's KalleHorse 3"],
      [`/o/${ORG}/invoices/i1`, "Invoice 1001Anna Andersson"],
    ]);
    expect(screen.getByRole("group", { name: "Customers" })).toBeInTheDocument();
    expect(mocked.mock.calls.map((call) => call[1])).toContain("/customers?q=anna&limit=5");
  });

  it("leaves a group out when the backend refuses it, and says so when nothing is found", async () => {
    backend({ "/customers": [{ id: "c1", name: "Zed", number: 1 }] }); // everything else answers 403
    render(<QuickSearch orgId={ORG} pages={[]} />);
    await type("zed");
    expect(await screen.findByTestId("quick-search-hit")).toHaveTextContent("Zed");
    await userEvent.clear(screen.getByTestId("quick-search-input"));
    backend({}); // nothing matches the next text, and everything is refused
    await type("qq");
    await waitFor(() => expect(screen.getByTestId("quick-search-empty")).toHaveTextContent("Nothing found."));
  });

  it("looks up an order by its number when digits are typed", async () => {
    backend({ "/transactions": [{ id: "t1", number: 1004 }] });
    render(<QuickSearch orgId={ORG} pages={[]} />);
    await type("1004");
    const hit = await screen.findByRole("option", { name: "Order 1004" });
    expect(hit).toHaveAttribute("href", `/o/${ORG}/transactions/t1`);
    expect(mocked.mock.calls.map((call) => call[1])).toContain("/transactions?number=1004&limit=5");
  });

  it("asks nothing for fewer than two letters", async () => {
    render(<QuickSearch orgId={ORG} pages={PAGES} />);
    await type("a");
    expect(mocked).not.toHaveBeenCalled();
  });

  it("moves with the arrow keys and Escape closes it", async () => {
    render(<QuickSearch orgId={ORG} pages={PAGES} />);
    await type("rs"); // Customers and Orders
    const options = screen.getAllByRole("option");
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("focuses with / and Ctrl+K from anywhere on the page, but not while typing in a field", async () => {
    render(
      <>
        <input aria-label="other" />
        <QuickSearch orgId={ORG} pages={PAGES} />
      </>,
    );
    fireEvent.keyDown(document.body, { key: "/" });
    expect(screen.getByTestId("quick-search-input")).toHaveFocus();
    (screen.getByTestId("quick-search-input") as HTMLInputElement).blur();
    fireEvent.keyDown(document.body, { key: "k", ctrlKey: true });
    expect(screen.getByTestId("quick-search-input")).toHaveFocus();
    screen.getByLabelText("other").focus();
    fireEvent.keyDown(screen.getByLabelText("other"), { key: "/" });
    expect(screen.getByLabelText("other")).toHaveFocus(); // a slash typed into another field stays there
    await waitFor(() => expect(true).toBe(true));
  });
});
