import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  Harness,
  ITEM_ID,
  LINE_1,
  LINE_2,
  ORG_A,
  TX_ID,
  deferred,
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
import type { MoneyString, QuantityString } from "@/lib/decimal";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(tx());
  installBackend(() => ok(line()));
});

const SADDLE = "77777777-7777-4777-8777-777777777777";
const itemPicker = () => within(screen.getByTestId("picker-item_id"));

async function openAdd(kind: "Catalog item" | "Ad-hoc line" = "Catalog item") {
  await userEvent.click(screen.getByTestId("add-line"));
  if (kind === "Ad-hoc line") await userEvent.click(screen.getByLabelText("Ad-hoc line"));
}

async function chooseItem(name: RegExp) {
  await userEvent.click(itemPicker().getByRole("combobox"));
  await userEvent.click(await itemPicker().findByRole("option", { name }));
}

const rowOf = (id: string) => document.querySelector<HTMLElement>(`[data-line-id="${id}"]`)!;

async function openEditor(id = LINE_1) {
  await userEvent.click(within(rowOf(id)).getByTestId("edit-line"));
  return within(screen.getByTestId("line-editor"));
}

async function replace(label: string, value: string) {
  const field = screen.getByLabelText(label, { exact: true });
  await userEvent.clear(field);
  await userEvent.type(field, value);
}

describe("adding a line from the catalog", () => {
  it("sends ONLY the item's id and the quantity: the server makes the snapshot", async () => {
    render(<Harness initial={tx()} />);
    await openAdd();

    await chooseItem(/Saddle fitting/);
    await userEvent.type(screen.getByLabelText("Quantity"), "2.5");
    await userEvent.click(screen.getByTestId("submit-line"));

    const [call] = writes();
    expect(call).toEqual({ method: "POST", path: `/transactions/${TX_ID}/lines`, body: { item_id: SADDLE, quantity: "2.5" }, ifMatch: undefined, orgId: ORG_A });
    expect(Object.keys(call.body as object).sort()).toEqual(["item_id", "quantity"]); // no name, unit, price or VAT copied from the item
    expect(JSON.stringify(call.body)).not.toMatch(/1200|hour|Saddle|vat|price|description|unit/i);
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("the choices come from the active items only, and the item's id (not its text) is what is chosen", async () => {
    render(<Harness initial={tx()} />);
    await openAdd();
    await userEvent.click(itemPicker().getByRole("combobox"));
    await itemPicker().findAllByRole("option");

    expect(searches().some((path) => path.startsWith("/items?") && new URL(path, "http://x").searchParams.get("active") === "true")).toBe(true);
    expect(searches().filter((path) => path.startsWith("/items?")).every((path) => new URL(path, "http://x").searchParams.get("active") === "true")).toBe(true);
    const hidden = document.querySelector<HTMLInputElement>('input[type="hidden"][name="item_id"]')!;
    expect(hidden.value).toBe("");
    await userEvent.click(itemPicker().getByRole("option", { name: /Horse massage/ }));
    expect(hidden.value).toBe(ITEM_ID);
  });

  it("closes after adding and the line appears when the server sends it", async () => {
    server.tx = tx({ lines: [line(), second(), line({ id: "99999999-9999-4999-8999-999999999999", position: 3, description: "Saddle fitting" })], line_count: 3, version: 5 });
    render(<Harness initial={tx()} />);
    await openAdd();
    await chooseItem(/Saddle fitting/);
    await userEvent.type(screen.getByLabelText("Quantity"), "1");
    await userEvent.click(screen.getByTestId("submit-line"));

    await waitFor(() => expect(screen.getAllByTestId("line-row")).toHaveLength(3));
    expect(screen.queryByTestId("add-line-form")).toBeNull();
    expect(screen.getByTestId("complete")).toBeEnabled();
  });

  it("without an item nothing is sent: 'Choose an item.' on the picker", async () => {
    render(<Harness initial={tx()} />);
    await openAdd();
    await userEvent.type(screen.getByLabelText("Quantity"), "1");

    await userEvent.click(screen.getByTestId("submit-line"));

    expect(screen.getByTestId("error-item_id")).toHaveTextContent("Choose an item.");
    expect(writes()).toHaveLength(0);
  });

  it.each(["", "abc", "1,5", "-1", "1e2"])("a quantity that is not a decimal at all (%j) is stopped locally", async (typed) => {
    render(<Harness initial={tx()} />);
    await openAdd();
    await chooseItem(/Horse massage/);
    if (typed !== "") await userEvent.type(screen.getByLabelText("Quantity"), typed);

    await userEvent.click(screen.getByTestId("submit-line"));

    expect(screen.getByTestId("error-quantity")).toHaveTextContent("Enter a number such as 850.00");
    expect(writes()).toHaveLength(0);
  });

  it.each(["0", "0.0001", "9999999999999", "1000000000", "0.000"])("a decimal the BACKEND may refuse (%j) is sent as typed and judged there", async (typed) => {
    installBackend(() => invalid(["quantity", "Input should be greater than 0"]));
    render(<Harness initial={tx()} />);
    await openAdd();
    await chooseItem(/Horse massage/);
    await userEvent.type(screen.getByLabelText("Quantity"), typed);

    await userEvent.click(screen.getByTestId("submit-line"));

    expect((writes()[0].body as { quantity: string }).quantity).toBe(typed);
    expect(await screen.findByTestId("error-quantity")).toHaveTextContent("greater than 0");
  });

  it("maps the backend's 422 to the Item picker (an inactive or unknown item) and keeps the draft open", async () => {
    installBackend(() => invalid(["item_id", "Item is inactive"]));
    render(<Harness initial={tx()} />);
    await openAdd();
    await chooseItem(/Horse massage/);
    await userEvent.type(screen.getByLabelText("Quantity"), "1");

    await userEvent.click(screen.getByTestId("submit-line"));

    expect(await screen.findByTestId("error-item_id")).toHaveTextContent("Item is inactive");
    expect(itemPicker().getByRole("combobox")).toHaveAttribute("aria-invalid", "true");
    expect(itemPicker().getByRole("combobox")).toHaveValue("Horse massage"); // still chosen, so the user can change it
    expect(screen.getByLabelText("Quantity")).toHaveValue("1");
    expect(router.refresh).not.toHaveBeenCalled(); // nothing was added
  });

  it("an amount too large is the backend's call, shown on the quantity", async () => {
    installBackend(() => invalid(["quantity", "The line amount is too large"]));
    render(<Harness initial={tx()} />);
    await openAdd();
    await chooseItem(/Horse massage/);
    await userEvent.type(screen.getByLabelText("Quantity"), "999999999");
    await userEvent.click(screen.getByTestId("submit-line"));
    expect(await screen.findByTestId("error-quantity")).toHaveTextContent("The line amount is too large");
  });

  it("if the transaction was completed elsewhere it says so, and the read-only state is shown", async () => {
    installBackend(() => fail(409, { detail: "A completed transaction cannot be changed; reopen it first" }));
    server.tx = tx({ status: "completed", version: 5 });
    render(<Harness initial={tx()} />);
    await openAdd();
    await chooseItem(/Horse massage/);
    await userEvent.type(screen.getByLabelText("Quantity"), "1");

    await userEvent.click(screen.getByTestId("submit-line"));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("no longer a draft, so your changes could not be saved");
    await waitFor(() => expect(screen.queryByTestId("add-line-form")).toBeNull());
    expect(screen.queryByTestId("add-line")).toBeNull();
  });

  it("Cancel closes the form without sending anything", async () => {
    render(<Harness initial={tx()} />);
    await openAdd();
    await userEvent.click(screen.getByTestId("cancel-add-line"));
    expect(screen.queryByTestId("add-line-form")).toBeNull();
    expect(writes()).toHaveLength(0);
  });
});

describe("adding an ad-hoc line", () => {
  async function fillAdHoc(values: { description?: string; unit?: string; quantity?: string; price?: string; vat?: string }) {
    await openAdd("Ad-hoc line");
    if (values.description) await userEvent.type(screen.getByLabelText("Description"), values.description);
    if (values.unit) await userEvent.type(screen.getByLabelText("Unit"), values.unit);
    if (values.quantity) await userEvent.type(screen.getByLabelText("Quantity"), values.quantity);
    if (values.price) await userEvent.type(screen.getByLabelText("Unit price excluding VAT"), values.price);
    if (values.vat) await userEvent.type(screen.getByLabelText("VAT rate (%)"), values.vat);
  }

  it.each([
    ["0.10", "8.20", "4.35"],
    ["9999999999.99", "100", "0.001"],
    ["850", "25.00", "2"],
  ])("sends price %s, VAT %s and quantity %s as the strings typed, with no item and no number", async (price, vat, quantity) => {
    render(<Harness initial={tx()} />);
    await fillAdHoc({ description: "Travel", unit: "km", quantity, price, vat });

    await userEvent.click(screen.getByTestId("submit-line"));

    const body = writes()[0].body as Record<string, unknown>;
    expect(body).toEqual({ description: "Travel", unit: "km", quantity, unit_price_ex_vat: price, vat_rate: vat });
    expect("item_id" in body).toBe(false);
    expect(Object.values(body).every((value) => typeof value === "string")).toBe(true);
    expect(JSON.stringify(body)).toContain(`"unit_price_ex_vat":"${price}"`);
    expect(writes()[0].ifMatch).toBeUndefined();
  });

  it.each([["unit_price_ex_vat", "Unit price excluding VAT"], ["vat_rate", "VAT rate (%)"]])("a %s that is not a decimal is stopped locally", async (key, label) => {
    render(<Harness initial={tx()} />);
    await fillAdHoc({ description: "Travel", unit: "km", quantity: "1", price: key === "unit_price_ex_vat" ? "1,5" : "10", vat: key === "vat_rate" ? "abc" : "25" });
    await userEvent.click(screen.getByTestId("submit-line"));
    expect(screen.getByTestId(`error-${key}`)).toHaveTextContent("Enter a number such as 850.00");
    expect(screen.getByLabelText(label)).toHaveAttribute("aria-invalid", "true");
    expect(writes()).toHaveLength(0);
  });

  it("the backend judges the rest: 422 on each control, and a whole-body message in the summary", async () => {
    installBackend(() =>
      fail(422, {
        detail: [
          { loc: ["body", "description"], msg: "String should have at least 1 character", type: "x" },
          { loc: ["body", "vat_rate"], msg: "Input should be less than or equal to 100", type: "x" },
          { loc: ["body"], msg: "Value error, without item_id these are required: unit", type: "x" },
        ],
      }),
    );
    render(<Harness initial={tx()} />);
    await fillAdHoc({ quantity: "1", price: "10", vat: "101" });

    await userEvent.click(screen.getByTestId("submit-line"));

    expect(await screen.findByTestId("error-description")).toBeInTheDocument();
    expect(screen.getByTestId("error-vat_rate")).toHaveTextContent("less than or equal to 100");
    expect(screen.getByTestId("form-error")).toHaveTextContent("without item_id these are required: unit");
    expect(screen.queryByTestId("error-unit")).toBeNull();
    expect(screen.getByLabelText("VAT rate (%)")).toHaveValue("101"); // draft kept
  });

  it("switching kind keeps what the user typed in the quantity", async () => {
    render(<Harness initial={tx()} />);
    await openAdd();
    await userEvent.type(screen.getByLabelText("Quantity"), "3");
    await userEvent.click(screen.getByLabelText("Ad-hoc line"));
    expect(screen.getByLabelText("Quantity")).toHaveValue("3");
  });
});

describe("editing a line", () => {
  it("opens with the stored snapshot values, not the catalog's", async () => {
    render(<Harness initial={tx({ lines: [line({ description: "Old name", unit: "visit", unit_price_ex_vat: "0.10" as MoneyString }), second()] })} />);

    await openEditor();

    expect(screen.getByLabelText("Description")).toHaveValue("Old name");
    expect(screen.getByLabelText("Unit")).toHaveValue("visit");
    expect(screen.getByLabelText("Unit price excluding VAT")).toHaveValue("0.10");
    expect(screen.getByLabelText("Quantity")).toHaveValue("1.000");
    expect(vi.mocked(apiFetch).mock.calls.filter(([, path]) => path.startsWith("/items"))).toHaveLength(0); // no item was fetched
  });

  it("sends only the fields that changed, as strings, with the line's version, and never an item", async () => {
    server.tx = tx({ version: 5, lines: [line({ version: 2, quantity: "2.500" as QuantityString }), second()] });
    render(<Harness initial={tx({ lines: [line({ version: 1 }), second()] })} />);
    await openEditor();

    await replace("Quantity", "2.5");
    await replace("Unit price excluding VAT", "8.20");
    await userEvent.click(screen.getByTestId("save-line"));

    const [call] = writes();
    expect(call).toEqual({ method: "PATCH", path: `/transactions/${TX_ID}/lines/${LINE_1}`, body: { quantity: "2.5", unit_price_ex_vat: "8.20" }, ifMatch: 1, orgId: ORG_A });
    expect("item_id" in (call.body as object)).toBe(false);
    expect(router.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByTestId("line-editor")).toBeNull());
    await waitFor(() => expect(within(rowOf(LINE_1)).getByTestId("line-quantity")).toHaveTextContent("2.500")); // the server's number, as sent
  });

  it("an override changes the line, and no request ever mentions the catalog item", async () => {
    render(<Harness initial={tx()} />);
    await openEditor();
    await replace("Description", "Custom description");
    await replace("VAT rate (%)", "6");

    await userEvent.click(screen.getByTestId("save-line"));

    expect(writes()).toHaveLength(1);
    expect(writes().every((call) => call.path.startsWith("/transactions/"))).toBe(true);
    expect(vi.mocked(apiFetch).mock.calls.some(([, path]) => path.startsWith("/items/") || path.includes(ITEM_ID))).toBe(false);
  });

  it.each(["", "abc", "1,5", "-1", "1e2"])("a changed decimal that is not a decimal at all (%j) is stopped locally on its control", async (typed) => {
    render(<Harness initial={tx()} />);
    await openEditor();
    const field = screen.getByLabelText("Unit price excluding VAT");
    await userEvent.clear(field);
    if (typed !== "") await userEvent.type(field, typed);

    await userEvent.click(screen.getByTestId("save-line"));

    expect(screen.getByTestId("error-unit_price_ex_vat")).toHaveTextContent("Enter a number such as 850.00");
    expect(writes()).toHaveLength(0);
    expect(screen.getByTestId("line-editor")).toBeInTheDocument();
  });

  it("makes no request when nothing changed", async () => {
    render(<Harness initial={tx()} />);
    await openEditor();
    await userEvent.click(screen.getByTestId("save-line"));
    expect(writes()).toHaveLength(0);
    expect(screen.queryByTestId("line-editor")).toBeNull();
  });

  it("a rejected edit keeps the draft and shows the backend's message on the right control", async () => {
    installBackend(() => invalid(["quantity", "Input should be greater than 0"], ["description", "String should have at least 1 character"]));
    render(<Harness initial={tx()} />);
    await openEditor();
    await replace("Quantity", "0");
    await userEvent.clear(screen.getByLabelText("Description"));

    await userEvent.click(screen.getByTestId("save-line"));

    expect(await screen.findByTestId("error-quantity")).toHaveTextContent("greater than 0");
    expect(screen.getByTestId("error-description")).toBeInTheDocument();
    expect(screen.queryByTestId("error-unit")).toBeNull();
    expect(screen.getByLabelText("Quantity")).toHaveValue("0");
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("another line's editor and the rest of the page wait while this one saves, and show its own progress", async () => {
    const saving = deferred<ReturnType<typeof ok>>();
    installBackend(() => saving.promise);
    render(<Harness initial={tx()} />);
    await openEditor();
    await replace("Description", "Changed");

    await userEvent.click(screen.getByTestId("save-line"));

    expect(screen.getByTestId("save-line")).toHaveTextContent("Saving…");
    expect(screen.getByTestId("save-line")).toBeDisabled();
    expect(within(rowOf(LINE_2)).getByTestId("edit-line")).toBeDisabled();
    expect(within(rowOf(LINE_2)).getByTestId("delete-line")).toBeDisabled();
    await act(async () => saving.resolve(ok(line())));
  });

  it("editing one line does not make another line's editor stale", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(within(rowOf(LINE_2)).getByTestId("edit-line"));
    await replace("Description", "Second draft");

    // The first line is saved elsewhere in this tab (its version moves; the second line's does not).
    server.tx = tx({ version: 5, lines: [line({ version: 2, description: "Changed" }), second({ version: 1 })] });
    await act(async () => router.refresh());

    expect(screen.queryByTestId("line-conflict")).toBeNull();
    expect(screen.getByTestId("save-line")).toBeEnabled();
    expect(screen.getByLabelText("Description")).toHaveValue("Second draft");
  });
});

describe("a line that changed elsewhere", () => {
  it("when FastAPI refuses the version, the draft is kept and saving is off; the latest is loaded around it, with what is there now", async () => {
    installBackend(() => stale(3));
    server.tx = tx({ version: 5, lines: [line({ version: 3, description: "Edited elsewhere" }), second()] });
    render(<Harness initial={tx()} />);
    await openEditor();
    await replace("Description", "My careful edit");
    await replace("Quantity", "7");

    await userEvent.click(screen.getByTestId("save-line"));

    const conflict = await screen.findByTestId("line-conflict");
    expect(conflict).toHaveTextContent("This line was changed elsewhere. Your edits were not saved.");
    expect(screen.getByLabelText("Description")).toHaveValue("My careful edit"); // not destroyed
    expect(screen.getByLabelText("Quantity")).toHaveValue("7");
    expect(screen.getByTestId("save-line")).toBeDisabled();
    expect(router.refresh).toHaveBeenCalledTimes(1); // the page around the editor is brought up to date...
    await waitFor(() => expect(screen.getByTestId("line-conflict")).toHaveTextContent("Edited elsewhere")); // ...so the notice can say what is there now
    expect(screen.getByLabelText("Description")).toHaveValue("My careful edit"); // ...and the draft is still the user's
    expect(screen.queryByTestId("editor-notice")).toBeNull();
    expect(writes()).toHaveLength(1); // and it did not retry
  });

  it("the user chooses: 'Discard my edits and load the latest' closes the editor and refreshes", async () => {
    installBackend(() => stale(3));
    server.tx = tx({ version: 5, lines: [line({ version: 3, description: "Edited elsewhere" }), second()] });
    render(<Harness initial={tx()} />);
    await openEditor();
    await replace("Description", "Mine");
    await userEvent.click(screen.getByTestId("save-line"));
    await screen.findByTestId("line-conflict");
    router.refresh.mockClear();

    await userEvent.click(screen.getByTestId("discard-line"));

    expect(router.refresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("line-editor")).toBeNull();
    await waitFor(() => expect(within(rowOf(LINE_1)).getByTestId("line-description")).toHaveTextContent("Edited elsewhere"));
    expect(document.body.textContent).not.toContain("Mine");
  });

  it("when the server's line has already moved on, the conflict shows at once, with what is there now, and the draft stays", async () => {
    render(<Harness initial={tx()} />);
    await openEditor();
    await replace("Description", "Half-written edit");

    server.tx = tx({ version: 5, lines: [line({ version: 4, description: "Changed in another tab", quantity: "9.000" as QuantityString }), second()] });
    await act(async () => router.refresh());

    const conflict = screen.getByTestId("line-conflict");
    expect(conflict).toHaveTextContent("changed elsewhere");
    expect(conflict).toHaveTextContent("Changed in another tab");
    expect(conflict).toHaveTextContent("9.000");
    expect(screen.getByLabelText("Description")).toHaveValue("Half-written edit");
    expect(screen.getByTestId("save-line")).toBeDisabled();
    expect(writes()).toHaveLength(0); // found out without sending anything
  });

  it("a save is judged by the version the edit was OPENED on, not the newest the screen has", async () => {
    render(<Harness initial={tx({ lines: [line({ version: 1 }), second()] })} />);
    await openEditor();
    await replace("Description", "Mine");
    server.tx = tx({ version: 5, lines: [line({ version: 2 }), second()] });
    await act(async () => router.refresh());

    // The conflict switches Save off, so a stale save can never be sent from this state.
    expect(screen.getByTestId("save-line")).toBeDisabled();
    await userEvent.click(screen.getByTestId("save-line"));
    expect(writes()).toHaveLength(0);
  });

  it("a deleted line shows 'no longer exists' and refreshes", async () => {
    installBackend(() => fail(404, { detail: "Not found" }));
    server.tx = tx({ lines: [second()], line_count: 1, version: 6 });
    render(<Harness initial={tx()} />);
    await openEditor();
    await replace("Description", "Too late");

    await userEvent.click(screen.getByTestId("save-line"));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("no longer exists");
    expect(router.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getAllByTestId("line-row")).toHaveLength(1));
  });
});

describe("deleting a line", () => {
  it("asks first; Keep does nothing", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(within(rowOf(LINE_1)).getByTestId("delete-line"));
    expect(screen.getByText("Delete this line?")).toBeInTheDocument();
    await userEvent.click(within(rowOf(LINE_1)).getByTestId("delete-line-keep"));
    expect(writes()).toHaveLength(0);
  });

  it("sends the version of the line as displayed, then refreshes", async () => {
    installBackend(() => ok(undefined, 204));
    server.tx = tx({ lines: [second()], line_count: 1, version: 5 });
    render(<Harness initial={tx({ lines: [line({ version: 3 }), second()] })} />);

    await userEvent.click(within(rowOf(LINE_1)).getByTestId("delete-line"));
    await userEvent.click(within(rowOf(LINE_1)).getByTestId("delete-line-confirm"));

    expect(writes()).toEqual([{ method: "DELETE", path: `/transactions/${TX_ID}/lines/${LINE_1}`, body: undefined, ifMatch: 3, orgId: ORG_A }]);
    await waitFor(() => expect(screen.getAllByTestId("line-row")).toHaveLength(1));
    expect(rowOf(LINE_1)).toBeNull();
  });

  it("a stale delete is refused and explained, and the latest is loaded; the line is still there", async () => {
    installBackend(() => stale(2));
    server.tx = tx({ version: 5, lines: [line({ version: 2, description: "Edited elsewhere" }), second()] });
    render(<Harness initial={tx()} />);

    await userEvent.click(within(rowOf(LINE_1)).getByTestId("delete-line"));
    await userEvent.click(within(rowOf(LINE_1)).getByTestId("delete-line-confirm"));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("changed elsewhere, so nothing was changed");
    expect(router.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(within(rowOf(LINE_1)).getByTestId("line-description")).toHaveTextContent("Edited elsewhere"));
  });

  it("a line that is already gone is reported, not treated as an error page", async () => {
    installBackend(() => fail(404, { detail: "Not found" }));
    server.tx = tx({ lines: [second()], line_count: 1, version: 6 });
    render(<Harness initial={tx()} />);
    await userEvent.click(within(rowOf(LINE_1)).getByTestId("delete-line"));
    await userEvent.click(within(rowOf(LINE_1)).getByTestId("delete-line-confirm"));
    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("no longer exists");
    await waitFor(() => expect(screen.getAllByTestId("line-row")).toHaveLength(1));
  });
});
