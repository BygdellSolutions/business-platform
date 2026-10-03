import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  Harness,
  LINE_1,
  LINE_2,
  ORG_A,
  ORG_B,
  TX_ID,
  deferred,
  fail,
  installBackend,
  line,
  notDraft,
  ok,
  resetServer,
  router,
  second,
  server,
  stale,
  tx,
  writes,
} from "@/features/transactions/testing";
import type { MoneyString, PercentString, QuantityString } from "@/lib/decimal";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(tx());
  installBackend(() => ok(null));
});

const status = () => screen.getByTestId("tx-status");
const text = (testId: string) => screen.getByTestId(testId).textContent;

describe("what the editor shows", () => {
  it("shows a draft with its header, lines, totals and the actions that can follow", () => {
    render(<Harness initial={tx()} />);

    expect(status()).toHaveTextContent("Draft");
    expect(text("header-customer")).toContain("Anna Andersson");
    expect(text("header-date")).toBe("2026-10-01");
    expect(screen.getAllByTestId("line-row")).toHaveLength(2);
    expect(screen.getByTestId("edit-header")).toBeEnabled();
    expect(screen.getByTestId("add-line")).toBeEnabled();
    expect(screen.getByTestId("complete")).toBeEnabled();
    expect(screen.getByTestId("cancel")).toBeEnabled();
    expect(screen.queryByTestId("reopen")).toBeNull();
  });

  it("shows every line as stored, in the server's order, numbered by position in the list", () => {
    render(<Harness initial={tx({ lines: [line({ position: 1 }), second({ position: 7 })] })} />); // a gap after deletes

    const rows = screen.getAllByTestId("line-row");
    expect(rows.map((row) => row.getAttribute("data-line-id"))).toEqual([LINE_1, LINE_2]);
    expect(within(rows[0]).getAllByRole("cell")[0]).toHaveTextContent("1");
    expect(within(rows[1]).getAllByRole("cell")[0]).toHaveTextContent("2");
    expect(within(rows[1]).getByTestId("line-description")).toHaveTextContent("Travel");
    expect(within(rows[1]).getByTestId("line-unit")).toHaveTextContent("km");
  });

  it("prints decimals exactly as the server sent them: no rounding, no reformatting, no arithmetic", () => {
    const awkward = tx({
      lines: [
        line({
          quantity: "2.375" as QuantityString,
          unit_price_ex_vat: "0.10" as MoneyString,
          vat_rate: "8.20" as PercentString,
          net_amount: "9999999999.99" as MoneyString,
          vat_amount: "0.30" as MoneyString,
          gross_amount: "4.35" as MoneyString,
        }),
      ],
      line_count: 1,
      // Deliberately NOT the sums of the lines: if the UI computed anything, these would differ.
      totals: {
        net_amount: "123.45" as MoneyString,
        vat_amount: "0.07" as MoneyString,
        gross_amount: "0.10" as MoneyString,
        vat_breakdown: [{ vat_rate: "8.20" as PercentString, net_amount: "7.00" as MoneyString, vat_amount: "8.20" as MoneyString }],
      },
    });

    render(<Harness initial={awkward} />);

    const row = screen.getByTestId("line-row");
    expect(within(row).getByTestId("line-quantity")).toHaveTextContent(/^2\.375$/);
    expect(within(row).getByTestId("line-price")).toHaveTextContent(/^0\.10$/);
    expect(within(row).getByTestId("line-vat-rate")).toHaveTextContent(/^8\.20$/);
    expect(within(row).getByTestId("line-net")).toHaveTextContent(/^9999999999\.99$/);
    expect(within(row).getByTestId("line-vat")).toHaveTextContent(/^0\.30$/);
    expect(within(row).getByTestId("line-gross")).toHaveTextContent(/^4\.35$/);
    expect(text("total-net")).toBe("123.45");
    expect(text("total-vat")).toBe("0.07");
    expect(text("total-gross")).toBe("0.10");
    const breakdown = within(screen.getByTestId("vat-row"));
    expect(breakdown.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["8.20", "7.00", "8.20"]);
  });

  it("shows the VAT breakdown rows in the order the server sent", () => {
    render(<Harness initial={tx()} />);
    const rows = screen.getAllByTestId("vat-row").map((row) => row.textContent);
    expect(rows).toEqual(["6.0043.752.63", "25.00850.00212.50"]);
  });

  it("shows only a link for the catalog item and never asks the backend about it", () => {
    render(<Harness initial={tx()} />);

    const [catalogLine, adHocLine] = screen.getAllByTestId("line-row");
    expect(within(catalogLine).getByRole("link", { name: "Catalog item" })).toHaveAttribute("href", expect.stringMatching(new RegExp(`^/o/${ORG_A}/catalog/`)));
    expect(within(adHocLine).getByText("Ad-hoc")).toBeInTheDocument();
    expect(vi.mocked(apiFetch)).not.toHaveBeenCalled(); // no item (or anything else) was fetched to render the lines
  });

  it("says when there are no lines and shows server zeros as they are", () => {
    render(
      <Harness
        initial={tx({
          lines: [],
          line_count: 0,
          totals: { net_amount: "0.00" as MoneyString, vat_amount: "0.00" as MoneyString, gross_amount: "0.00" as MoneyString, vat_breakdown: [] },
        })}
      />,
    );
    expect(screen.getByTestId("no-lines")).toBeInTheDocument();
    expect(screen.queryByTestId("vat-breakdown")).toBeNull();
    expect(text("total-gross")).toBe("0.00");
  });

  it("marks a customer that was deactivated since", () => {
    render(<Harness initial={tx({ billing_customer: { id: "x", name: "Old Customer", active: false } })} />);
    expect(text("header-customer")).toContain("(inactive)");
  });
});

describe("completed and cancelled transactions are read-only", () => {
  it("a completed one offers Reopen and Cancel and no editing at all", () => {
    render(<Harness initial={tx({ status: "completed" })} />);

    expect(status()).toHaveTextContent("Completed");
    expect(screen.getByTestId("status-note")).toHaveTextContent(/read-only/);
    expect(screen.getByTestId("reopen")).toBeEnabled();
    expect(screen.getByTestId("cancel")).toBeEnabled();
    expect(screen.queryByTestId("complete")).toBeNull();
    for (const id of ["edit-header", "add-line", "edit-line", "delete-line"]) expect(screen.queryByTestId(id)).toBeNull();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("combobox")).toHaveLength(0);
    expect(screen.getAllByTestId("line-row")).toHaveLength(2); // still shown in full
  });

  it("a cancelled one offers nothing", () => {
    render(<Harness initial={tx({ status: "cancelled" })} />);

    expect(status()).toHaveTextContent("Cancelled");
    expect(screen.getByTestId("status-note")).toHaveTextContent(/final/);
    for (const id of ["complete", "reopen", "cancel", "edit-header", "add-line", "edit-line", "delete-line"]) expect(screen.queryByTestId(id)).toBeNull();
    expect(screen.getByTestId("lifecycle")).toHaveTextContent("No further actions.");
  });

  it("an open editor does not come back to life when the transaction is reopened later", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getAllByTestId("edit-line")[0]);
    expect(screen.getByTestId("line-editor")).toBeInTheDocument();

    server.tx = tx({ status: "completed", version: 5 });
    await act(async () => router.refresh());
    expect(screen.queryByTestId("line-editor")).toBeNull();

    server.tx = tx({ status: "draft", version: 6 });
    await act(async () => router.refresh());
    expect(screen.queryByTestId("line-editor")).toBeNull();
    expect(screen.getAllByTestId("line-row")).toHaveLength(2);
  });
});

describe("lifecycle", () => {
  it("Complete sends the transaction's version, then refreshes from the server", async () => {
    installBackend(() => ok(tx({ status: "completed", version: 5 })));
    server.tx = tx({ status: "completed", version: 5 });
    render(<Harness initial={tx()} />);

    await userEvent.click(screen.getByTestId("complete"));

    expect(writes()).toEqual([{ method: "POST", path: `/transactions/${TX_ID}/complete`, body: undefined, ifMatch: 4, orgId: ORG_A }]);
    expect(router.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(status()).toHaveTextContent("Completed"));
    expect(screen.queryByTestId("complete")).toBeNull();
  });

  it("Reopen sends the version of the completed transaction", async () => {
    installBackend(() => ok(null));
    render(<Harness initial={tx({ status: "completed", version: 7 })} />);
    await userEvent.click(screen.getByTestId("reopen"));
    expect(writes()[0]).toMatchObject({ path: `/transactions/${TX_ID}/reopen`, ifMatch: 7 });
  });

  it("Cancel asks first, does nothing on Keep, and sends the version only when confirmed", async () => {
    installBackend(() => ok(null));
    render(<Harness initial={tx()} />);

    await userEvent.click(screen.getByTestId("cancel"));
    expect(screen.getByText("Cancel this transaction? This is final.")).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
    await userEvent.click(screen.getByTestId("cancel-keep"));
    expect(writes()).toHaveLength(0);
    expect(screen.getByTestId("cancel")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("cancel"));
    await userEvent.click(screen.getByTestId("cancel-confirm"));
    expect(writes()).toEqual([{ method: "POST", path: `/transactions/${TX_ID}/cancel`, body: undefined, ifMatch: 4, orgId: ORG_A }]);
  });

  it("the buttons wait while any editor is open, with a reason, and come back when it closes", async () => {
    render(<Harness initial={tx()} />);

    await userEvent.click(screen.getByTestId("edit-header"));
    expect(screen.getByTestId("complete")).toBeDisabled();
    expect(screen.getByTestId("cancel")).toBeDisabled();
    expect(screen.getByTestId("lifecycle-hint")).toHaveTextContent("Save or cancel your open edit first.");

    await userEvent.click(screen.getByTestId("cancel-header"));
    expect(screen.getByTestId("complete")).toBeEnabled();
    expect(screen.queryByTestId("lifecycle-hint")).toBeNull();

    await userEvent.click(screen.getByTestId("add-line"));
    expect(screen.getByTestId("complete")).toBeDisabled();
    await userEvent.click(screen.getByTestId("cancel-add-line"));
    expect(screen.getByTestId("complete")).toBeEnabled();

    await userEvent.click(screen.getAllByTestId("edit-line")[0]);
    expect(screen.getByTestId("complete")).toBeDisabled();
  });

  it("a blocked step shows the backend's own reason and refreshes", async () => {
    installBackend(() => fail(409, { detail: "A transaction needs at least one line to be completed" }));
    render(<Harness initial={tx({ lines: [], line_count: 0 })} />);

    await userEvent.click(screen.getByTestId("complete"));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("A transaction needs at least one line to be completed");
    expect(router.refresh).toHaveBeenCalledTimes(1);
    expect(status()).toHaveTextContent("Draft");
  });

  it("a stale step is explained as 'changed elsewhere, nothing was changed' and the latest is loaded", async () => {
    installBackend(() => stale(5));
    server.tx = tx({ version: 5, line_count: 3 });
    render(<Harness initial={tx()} />);

    await userEvent.click(screen.getByTestId("complete"));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("changed elsewhere, so nothing was changed");
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("a step on a transaction that is no longer in that state explains the state and refreshes", async () => {
    installBackend(() => fail(409, { detail: "A completed transaction cannot be completed" }));
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("complete"));
    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("A completed transaction cannot be completed");
    expect(router.refresh).toHaveBeenCalled();
  });

  it("keeps the structured completion problems, names the line, and does not discard them", async () => {
    const problems = [
      { code: "custom_field.required", message: "is required", entity_type: "transaction_line", entity_id: LINE_2, field: "owner", label: "Owner" },
      { code: "custom_field.required", message: "is required", entity_type: "transaction", entity_id: TX_ID, field: "project", label: "Project" },
      { code: "x.y", message: "something else", entity_type: "transaction_line", entity_id: "not-shown", field: null, label: null },
    ];
    installBackend(() => fail(409, { detail: { code: "validation_failed", event: "complete", message: "The complete step was blocked: 3 problem(s) must be fixed first", total: 3, problems } }));
    render(<Harness initial={tx()} />);

    await userEvent.click(screen.getByTestId("complete"));

    const notice = await screen.findByTestId("editor-notice");
    expect(notice).toHaveTextContent("The complete step was blocked: 3 problem(s) must be fixed first");
    expect(within(screen.getByTestId("editor-problems")).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Line 2 · Owner: is required",
      "Transaction · Project: is required",
      "Record: something else",
    ]);
    expect(status()).toHaveTextContent("Draft");
    expect(screen.getByTestId("complete")).toBeEnabled(); // fix it, try again
  });

  it("a forbidden or failing request is shown and nothing else happens", async () => {
    installBackend(() => fail(403, { detail: "Your role in this organization does not allow this." }));
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("complete"));
    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("Your role in this organization does not allow this.");
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("runs one change at a time: a second click while one is running does nothing", async () => {
    const running = deferred<ReturnType<typeof ok>>();
    installBackend(() => running.promise);
    render(<Harness initial={tx()} />);

    await userEvent.click(screen.getByTestId("complete"));
    expect(screen.getByTestId("complete")).toBeDisabled();
    expect(screen.getByTestId("complete")).toHaveTextContent("Completing…");
    expect(screen.getByTestId("edit-header")).toBeDisabled();
    expect(screen.getByTestId("add-line")).toBeDisabled();
    expect(screen.getAllByTestId("edit-line")[0]).toBeDisabled();
    await userEvent.click(screen.getByTestId("cancel")).catch(() => {});

    expect(writes()).toHaveLength(1);
    await act(async () => running.resolve(ok(null)));
  });
});

describe("two changes in the same instant", () => {
  it("only one request is sent even when two clicks arrive before the screen has re-rendered", async () => {
    const running = deferred<ReturnType<typeof ok>>();
    installBackend(() => running.promise);
    render(<Harness initial={tx()} />);
    const complete = screen.getByTestId("complete");

    act(() => {
      complete.click(); // both clicks run before React can disable the button
      complete.click();
    });

    expect(writes()).toHaveLength(1);
    await act(async () => running.resolve(ok(null)));
  });

  it("a Complete and a Delete in the same instant: one wins, the other is not sent", async () => {
    const running = deferred<ReturnType<typeof ok>>();
    installBackend(() => running.promise);
    render(<Harness initial={tx()} />);
    const complete = screen.getByTestId("complete");
    const edit = screen.getAllByTestId("edit-line")[0];

    act(() => {
      complete.click();
      edit.click();
    });

    expect(writes()).toHaveLength(1);
    await act(async () => running.resolve(ok(null)));
  });
});

describe("authoritative refresh", () => {
  it("shows the totals as updating while the page re-reads the transaction, then the server's new ones", async () => {
    // The page "loads" until we open the gate (see Harness).
    const finished = deferred<void>();
    installBackend(() => ok(null));
    server.tx = tx({ version: 5, totals: { ...tx().totals, gross_amount: "2000.00" as MoneyString } });
    render(<Harness initial={tx()} />);
    server.gate = finished.promise; // from now on, re-reading the page takes a while

    await userEvent.click(screen.getByTestId("complete"));
    await waitFor(() => expect(screen.getByTestId("totals")).toHaveAttribute("data-updating"));
    expect(text("total-gross")).toBe("1108.88"); // still the old figure, but marked as stale
    expect(screen.getByTestId("totals")).toHaveTextContent("updating");

    await act(async () => finished.resolve());
    server.gate = null;
    await waitFor(() => expect(screen.getByTestId("totals")).not.toHaveAttribute("data-updating"));
    expect(text("total-gross")).toBe("2000.00");
  });
});

describe("a tab that comes back", () => {
  function becomeVisible(state: "visible" | "hidden") {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    document.dispatchEvent(new Event("visibilitychange"));
  }

  it("refreshes when it becomes visible and nothing is being edited", () => {
    render(<Harness initial={tx()} />);
    becomeVisible("visible");
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("does nothing while it is hidden", () => {
    render(<Harness initial={tx()} />);
    becomeVisible("hidden");
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it.each([
    ["the header editor", "edit-header"],
    ["a line editor", "edit-line"],
    ["the add-line form", "add-line"],
  ])("never refreshes over %s", async (_name, button) => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getAllByTestId(button)[0]);

    becomeVisible("visible");

    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("refreshes again once the editor is closed", async () => {
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("edit-header"));
    becomeVisible("visible");
    expect(router.refresh).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("cancel-header"));
    becomeVisible("visible");
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("does not interrupt a change that is running", async () => {
    const running = deferred<ReturnType<typeof ok>>();
    installBackend(() => running.promise);
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("complete"));

    becomeVisible("visible");

    expect(router.refresh).not.toHaveBeenCalled();
    await act(async () => running.resolve(ok(null)));
  });

  it("does not poll: nothing refreshes by itself over time", () => {
    vi.useFakeTimers();
    try {
      render(<Harness initial={tx()} />);
      vi.advanceTimersByTime(10 * 60 * 1000);
      expect(router.refresh).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops listening when the editor is gone", () => {
    const { unmount } = render(<Harness initial={tx()} />);
    unmount();
    becomeVisible("visible");
    expect(router.refresh).not.toHaveBeenCalled();
  });
});

describe("answers that arrive after the user has left", () => {
  it("a response that arrives after the editor was removed does not refresh or warn", async () => {
    const late = deferred<ReturnType<typeof ok>>();
    installBackend(() => late.promise);
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount } = render(<Harness initial={tx()} />);
    await userEvent.click(screen.getByTestId("complete"));

    unmount();
    await act(async () => late.resolve(ok(null)));

    expect(router.refresh).not.toHaveBeenCalled();
    expect(quiet).not.toHaveBeenCalled();
    quiet.mockRestore();
  });

  it("a change started in one organization is not followed by anything in the other after a switch", async () => {
    const late = deferred<ReturnType<typeof ok>>();
    installBackend(() => late.promise);
    const { rerender } = render(<Harness initial={tx()} orgId={ORG_A} />);
    await userEvent.click(screen.getByTestId("complete"));
    expect(writes()[0].orgId).toBe(ORG_A);

    rerender(<Harness initial={tx({ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", status: "completed" })} orgId={ORG_B} />);
    await act(async () => late.resolve(ok(null)));

    expect(router.refresh).not.toHaveBeenCalled();
    expect(writes()).toHaveLength(1); // nothing was sent for B
    expect(screen.queryByTestId("editor-notice")).toBeNull();
  });

  it("drafts typed in one organization do not exist after a switch to another", async () => {
    const { rerender } = render(<Harness initial={tx()} orgId={ORG_A} />);
    await userEvent.click(screen.getAllByTestId("edit-line")[0]);
    await userEvent.clear(screen.getByLabelText("Description"));
    await userEvent.type(screen.getByLabelText("Description"), "Typed in A");

    rerender(<Harness initial={tx()} orgId={ORG_B} />);

    expect(screen.queryByTestId("line-editor")).toBeNull();
    expect(screen.queryByDisplayValue("Typed in A")).toBeNull();
    expect(document.body.textContent).not.toContain("Typed in A");
  });
});

describe("a stale not-a-draft answer", () => {
  it("is explained once, with the backend's reason available, and the read-only state is shown", async () => {
    installBackend(() => notDraft());
    server.tx = tx({ status: "completed", version: 5 });
    render(<Harness initial={tx()} />);
    await userEvent.click(screen.getAllByTestId("edit-line")[0]);
    await userEvent.clear(screen.getByLabelText("Description"));
    await userEvent.type(screen.getByLabelText("Description"), "Will not be saved");
    await userEvent.click(screen.getByTestId("save-line"));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("no longer a draft, so your changes could not be saved");
    await waitFor(() => expect(status()).toHaveTextContent("Completed"));
    expect(screen.queryByTestId("line-editor")).toBeNull();
    expect(screen.queryByTestId("edit-line")).toBeNull();
  });
});
