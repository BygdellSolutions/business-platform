import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  Harness,
  INVOICE_ID,
  ORG_A,
  ORG_B,
  alreadyIssued,
  conflict,
  deferred,
  fail,
  installBackend,
  invalid,
  invoice,
  issued,
  network,
  ok,
  resetServer,
  router,
  server,
  stale,
  calls,
  writes,
} from "@/features/invoices/testing";
import type { Invoice } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(invoice());
  installBackend(() => ok(null));
});

const INVOICE_PATH = `/invoices/${INVOICE_ID}`;
const text = (testId: string) => screen.getByTestId(testId).textContent;
// An issued invoice has exactly one button: the PDF download. No control changes anything.
function expectOnlyTheDownload() {
  expect(screen.getAllByRole("button").map((button) => button.getAttribute("data-testid"))).toEqual(["download-pdf"]);
}

async function openEditor() {
  await userEvent.click(screen.getByTestId("edit-details"));
}
function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } });
}
async function confirm(testId: string) {
  await userEvent.click(screen.getByTestId(testId));
  await userEvent.click(screen.getByTestId(`${testId}-confirm`));
}
function visible() {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("editing a draft's details", () => {
  it("sends only the changed field, with the version the editor was opened on in If-Match, and shows the server's answer", async () => {
    installBackend(({ method }) => {
      if (method !== "PATCH") return ok(null);
      server.invoice = invoice({ description: "New text", version: 4 });
      return ok(server.invoice);
    });
    render(<Harness initial={invoice()} />);

    await openEditor();
    type("Description", "New text");
    await userEvent.click(screen.getByTestId("save-details"));

    expect(writes()).toEqual([{ method: "PATCH", path: INVOICE_PATH, body: { description: "New text" }, ifMatch: 3, orgId: ORG_A }]);
    await waitFor(() => expect(text("invoice-description")).toBe("New text"));
    expect(text("invoice-version")).toBe("4");
    expect(screen.queryByTestId("save-details")).toBeNull(); // the editor closed
  });

  it("shows the three fields and nothing else: no sources, no lines, no amounts are editable", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    expect(screen.getByLabelText("Invoice date")).toHaveValue("2026-10-01");
    expect(screen.getByLabelText("Due date")).toHaveValue("2026-10-31");
    expect(screen.getByLabelText("Description")).toHaveValue("October work");
    expect(screen.getAllByRole("textbox")).toHaveLength(1); // only the description is a text box; the dates are date inputs
    expect(document.querySelectorAll("input[type=date]")).toHaveLength(2);
    expect(screen.queryByLabelText(/quantity|price|currency|customer|amount/i)).toBeNull();
  });

  it("makes no request when nothing changed, so no version is moved just to have saved", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    await userEvent.click(screen.getByTestId("save-details"));
    expect(writes()).toEqual([]);
    expect(screen.queryByTestId("save-details")).toBeNull();
    expect(text("invoice-version")).toBe("3");
  });

  it("a change that is changed back is also no change", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "Something else");
    type("Description", "October work");
    await userEvent.click(screen.getByTestId("save-details"));
    expect(writes()).toEqual([]);
  });

  it("clearing the due date or the description sends null, never an empty string", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Due date", "");
    type("Description", "   ");
    await userEvent.click(screen.getByTestId("save-details"));
    expect(writes()[0].body).toEqual({ due_date: null, description: null });
  });

  it("an invoice date that is emptied (or not a date) is stopped on its control without a request", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    // A date input holds only a valid date or nothing; the required invoice date cannot be nothing.
    fireEvent.change(screen.getByLabelText("Invoice date"), { target: { value: "2026-13-45" } });
    await userEvent.click(screen.getByTestId("save-details"));

    expect(screen.getByTestId("error-invoice_date")).toHaveTextContent("Enter a date");
    expect(writes()).toEqual([]);
    expect(screen.getByTestId("save-details")).toBeEnabled(); // the user can correct it
  });

  it("shows the backend's 422 on the matching control and keeps what was typed", async () => {
    installBackend(({ method }) => (method === "PATCH" ? invalid(["due_date", "The due date cannot be before the invoice date"]) : ok(null)));
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Due date", "2026-09-01");
    await userEvent.click(screen.getByTestId("save-details"));

    expect(await screen.findByTestId("error-due_date")).toHaveTextContent("before the invoice date");
    expect(screen.getByLabelText("Due date")).toHaveValue("2026-09-01");
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("sends once however often Save is pressed in the same instant", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "PATCH" ? answer.promise : ok(null)));
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "Once");
    const form = screen.getByRole("form", { name: "Edit invoice details" });
    await act(async () => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    expect(writes()).toHaveLength(1);
    await act(async () => answer.resolve(ok(invoice({ description: "Once", version: 4 }))));
  });
});

describe("a stale edit", () => {
  it("is refused with the structured 409: the draft is kept, the server's state is shown, Save is off, and nothing is retried", async () => {
    installBackend(({ method }) => {
      if (method !== "PATCH") return ok(null);
      server.invoice = invoice({ version: 9, description: "Typed in another tab" });
      return stale();
    });
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "My unsaved text");
    await userEvent.click(screen.getByTestId("save-details"));

    const notice = await screen.findByTestId("header-conflict");
    expect(notice).toHaveTextContent("changed elsewhere");
    expect(notice).toHaveTextContent("Typed in another tab"); // the authoritative current state
    expect(screen.getByLabelText("Description")).toHaveValue("My unsaved text"); // the draft survives
    expect(screen.getByTestId("save-details")).toBeDisabled();
    expect(router.refresh).toHaveBeenCalled();
    expect(writes()).toHaveLength(1); // never retried, not even with the newer version
  });

  it("a newer version that arrives without any request (another tab, a refresh) switches Save off too, and keeps the draft", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "Mine");
    act(() => server.apply(invoice({ version: 5, description: "Theirs" })));

    expect(await screen.findByTestId("header-conflict")).toHaveTextContent("Theirs");
    expect(screen.getByLabelText("Description")).toHaveValue("Mine");
    expect(screen.getByTestId("save-details")).toBeDisabled();
    expect(writes()).toEqual([]);
  });

  it("discarding loads the latest and closes the editor, on the user's explicit choice", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "Mine");
    server.invoice = invoice({ version: 5, description: "Theirs" });
    act(() => server.apply(server.invoice));
    await userEvent.click(await screen.findByTestId("discard-details"));

    expect(screen.queryByTestId("save-details")).toBeNull();
    expect(text("invoice-description")).toBe("Theirs");
    expect(router.refresh).toHaveBeenCalled();
    expect(writes()).toEqual([]);
  });

  it("the edit is judged by the version it was OPENED on, never the newest one it can see", async () => {
    installBackend(({ method }) => (method === "PATCH" ? stale() : ok(null)));
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "Mine");
    act(() => server.apply(invoice({ version: 5 })));
    await screen.findByTestId("header-conflict");
    // Save is off, so no request can go out with version 5 on the user's behalf.
    await userEvent.click(screen.getByTestId("save-details"));
    expect(writes()).toEqual([]);
  });

  it("an invoice issued in another tab replaces the draft UI authoritatively: the editor closes and the issued document shows", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "Unsaved");
    act(() => server.apply(issued()));

    await waitFor(() => expect(screen.queryByTestId("save-details")).toBeNull());
    expect(text("invoice-status")).toBe("Issued");
    expect(text("invoice-number")).toBe("7");
    expect(screen.getByTestId("invoice-notice")).toHaveTextContent("no longer a draft");
    for (const control of ["issue", "delete-draft", "edit-details"]) expect(screen.queryByTestId(control)).toBeNull();
    expect(writes()).toEqual([]);
  });

  it("a PATCH answered 'issued' explains it and shows the issued invoice", async () => {
    installBackend(({ method }) => {
      if (method !== "PATCH") return ok(null);
      server.invoice = issued();
      return alreadyIssued();
    });
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "Too late");
    await userEvent.click(screen.getByTestId("save-details"));

    await waitFor(() => expect(text("invoice-status")).toBe("Issued"));
    expect(screen.getByTestId("invoice-notice")).toHaveTextContent("no longer a draft");
  });
});

describe("issuing", () => {
  it("asks first, and the question explains that an issued invoice cannot be edited or deleted", async () => {
    render(<Harness initial={invoice()} />);
    await userEvent.click(screen.getByTestId("issue"));

    expect(writes()).toEqual([]); // the first click only asks
    expect(screen.getByRole("group")).toHaveTextContent("gets its invoice number now");
    expect(screen.getByRole("group")).toHaveTextContent("cannot be edited or deleted");
  });

  it("declining sends nothing", async () => {
    render(<Harness initial={invoice()} />);
    await userEvent.click(screen.getByTestId("issue"));
    await userEvent.click(screen.getByTestId("issue-keep"));
    expect(writes()).toEqual([]);
    expect(text("invoice-status")).toBe("Draft");
  });

  it("posts with the current version, then shows the number the SERVER returned (none is predicted)", async () => {
    installBackend(({ method, path }) => {
      if (method === "POST" && path === `${INVOICE_PATH}/issue`) {
        server.invoice = issued({ number: 12, number_text: "12" });
        return ok(server.invoice);
      }
      return ok(null);
    });
    render(<Harness initial={invoice()} />);
    expect(screen.getByTestId("invoice-document").textContent).not.toMatch(/Invoice \d/);

    await confirm("issue");

    expect(writes()).toEqual([{ method: "POST", path: `${INVOICE_PATH}/issue`, body: undefined, ifMatch: 3, orgId: ORG_A }]);
    await waitFor(() => expect(text("invoice-heading")).toBe("Invoice 12"));
    expect(text("invoice-number")).toBe("12");
    expect(screen.getByTestId("invoice-notice")).toHaveTextContent("Invoice issued");
    expect(router.refresh).toHaveBeenCalled(); // the page was re-read, not patched locally
  });

  it("after issuing, the invoice is completely read-only", async () => {
    installBackend(({ method }) => {
      if (method === "POST") server.invoice = issued();
      return ok(server.invoice);
    });
    render(<Harness initial={invoice()} />);
    await confirm("issue");
    await waitFor(() => expect(text("invoice-status")).toBe("Issued"));

    for (const control of ["issue", "delete-draft", "edit-details", "save-details"]) expect(screen.queryByTestId(control)).toBeNull();
    expectOnlyTheDownload();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByTestId("issued-note")).toHaveTextContent("cannot be edited or deleted");
  });

  it.each([
    ["stale", () => stale(), /changed elsewhere/],
    ["source_changed", () => conflict("source_changed", "A source transaction was changed. The draft no longer matches."), /no longer matches this draft/],
    ["invoice_issued", () => alreadyIssued(), /no longer a draft/],
    ["a source lifecycle conflict", () => conflict("transactions_not_completed", "Only completed transactions can be invoiced"), /Only completed/],
  ])("explains a refused issuance (%s), re-reads the invoice and does not retry", async (_name, answer, message) => {
    installBackend(({ method }) => (method === "POST" ? answer() : ok(null)));
    render(<Harness initial={invoice()} />);
    await confirm("issue");

    expect(await screen.findByTestId("invoice-notice")).toHaveTextContent(message);
    expect(router.refresh).toHaveBeenCalled();
    expect(writes()).toHaveLength(1);
  });

  it("a stale refusal is followed by the NEXT attempt using the version now on the server, only when the user chooses it", async () => {
    let attempt = 0;
    installBackend(({ method }) => {
      if (method !== "POST") return ok(null);
      attempt += 1;
      if (attempt === 1) {
        server.invoice = invoice({ version: 9 });
        return stale();
      }
      server.invoice = issued({ version: 10 });
      return ok(server.invoice);
    });
    render(<Harness initial={invoice()} />);
    await confirm("issue");
    await screen.findByTestId("invoice-notice");
    await waitFor(() => expect(text("invoice-version")).toBe("9"));
    expect(writes()).toHaveLength(1);

    await confirm("issue");
    expect(writes().map((call) => call.ifMatch)).toEqual([3, 9]);
    await waitFor(() => expect(text("invoice-status")).toBe("Issued"));
  });

  it("ordinary validation errors are shown", async () => {
    installBackend(({ method }) => (method === "POST" ? invalid(["body", "Something is not valid"]) : ok(null)));
    render(<Harness initial={invoice()} />);
    await confirm("issue");
    expect(await screen.findByTestId("invoice-notice")).toHaveTextContent("Something is not valid");
  });

  it("a forbidden answer (the role was changed meanwhile) is shown as the backend's message", async () => {
    installBackend(({ method }) => (method === "POST" ? fail(403, { detail: "Your role in this organization does not allow this action" }) : ok(null)));
    render(<Harness initial={invoice()} />);
    await confirm("issue");
    expect(await screen.findByTestId("invoice-notice")).toHaveTextContent("does not allow this action");
  });

  it("one change at a time: two confirmations in the same instant issue once", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "POST" ? answer.promise : ok(null)));
    render(<Harness initial={invoice()} />);
    await userEvent.click(screen.getByTestId("issue"));
    const yes = screen.getByTestId("issue-confirm");
    await act(async () => {
      fireEvent.click(yes);
      fireEvent.click(yes);
    });
    expect(writes()).toHaveLength(1);
    await act(async () => answer.resolve(ok(issued())));
  });
});

describe("an unknown outcome is checked, not assumed", () => {
  it("a network failure while issuing: the invoice is re-read, and a committed issuance is shown as issued (never as 'still draft')", async () => {
    installBackend(({ method, path }) => {
      if (method === "POST") return network();
      if (method === "GET" && path === INVOICE_PATH) {
        server.invoice = issued({ number: 5, number_text: "5" });
        return ok(server.invoice);
      }
      return ok(null);
    });
    render(<Harness initial={invoice()} />);
    await confirm("issue");

    await waitFor(() => expect(text("invoice-status")).toBe("Issued"));
    expect(text("invoice-number")).toBe("5");
    expect(screen.getByTestId("invoice-notice")).toHaveTextContent("issued");
    expect(writes()).toHaveLength(1); // the issuance was NOT sent a second time
    expect(calls().some((call) => call.method === "GET" && call.path === INVOICE_PATH)).toBe(true);
  });

  it("while the outcome is unknown no new Issue attempt is offered; once checked and still a draft, it is", async () => {
    const check = deferred<ReturnType<typeof ok>>();
    installBackend(({ method, path }) => {
      if (method === "POST") return network();
      if (method === "GET" && path === INVOICE_PATH) return check.promise;
      return ok(null);
    });
    render(<Harness initial={invoice()} />);
    await confirm("issue");

    await waitFor(() => expect(screen.getByTestId("issue")).toBeDisabled());
    expect(screen.getByTestId("delete-draft")).toBeDisabled();
    await act(async () => check.resolve(ok(invoice()))); // the check answers: still the same draft

    await waitFor(() => expect(screen.getByTestId("issue")).toBeEnabled());
    expect(screen.getByTestId("invoice-notice")).toHaveTextContent("Checked");
    expect(writes()).toHaveLength(1);
  });

  it("if the check itself fails, nothing is offered until a check succeeds", async () => {
    let checks = 0;
    installBackend(({ method, path }) => {
      if (method === "POST") return network();
      if (method === "GET" && path === INVOICE_PATH) {
        checks += 1;
        return checks === 1 ? network() : ok(invoice());
      }
      return ok(null);
    });
    render(<Harness initial={invoice()} />);
    await confirm("issue");

    await waitFor(() => expect(screen.getByTestId("check-again")).toBeInTheDocument());
    expect(screen.getByTestId("issue")).toBeDisabled();
    await userEvent.click(screen.getByTestId("check-again"));

    await waitFor(() => expect(screen.getByTestId("issue")).toBeEnabled());
    expect(screen.queryByTestId("check-again")).toBeNull();
    expect(writes()).toHaveLength(1);
  });

  it("a server error (500) is just as unknown as a network failure", async () => {
    installBackend(({ method, path }) => {
      if (method === "POST") return fail(500, { detail: "Traceback" });
      if (method === "GET" && path === INVOICE_PATH) return ok(invoice({ version: 3 }));
      return ok(null);
    });
    render(<Harness initial={invoice()} />);
    await confirm("issue");
    await waitFor(() => expect(screen.getByTestId("invoice-notice")).toHaveTextContent("Checked"));
    expect(calls().filter((call) => call.method === "GET" && call.path === INVOICE_PATH)).toHaveLength(1);
  });

  it("an unknown outcome while deleting is checked too, and a draft that is gone ends in the not-found state", async () => {
    installBackend(({ method, path }) => {
      if (method === "DELETE") return network();
      if (method === "GET" && path === INVOICE_PATH) return fail(404, { detail: "Not found" });
      return ok(null);
    });
    render(<Harness initial={invoice()} />);
    await confirm("delete-draft");
    await waitFor(() => expect(screen.getByTestId("invoice-notice")).toHaveTextContent("no longer exists"));
    expect(router.refresh).toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled(); // no navigation on an unconfirmed deletion
  });
});

describe("deleting a draft", () => {
  it("asks first, and the question says the orders become invoiceable again", async () => {
    render(<Harness initial={invoice()} />);
    await userEvent.click(screen.getByTestId("delete-draft"));
    expect(writes()).toEqual([]);
    expect(screen.getByRole("group")).toHaveTextContent("orders become invoiceable again");
  });

  it("deletes with the current version and goes back to the list", async () => {
    installBackend(({ method }) => (method === "DELETE" ? ok(null, 204) : ok(null)));
    render(<Harness initial={invoice()} />);
    await confirm("delete-draft");

    expect(writes()).toEqual([{ method: "DELETE", path: INVOICE_PATH, body: undefined, ifMatch: 3, orgId: ORG_A }]);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/o/${ORG_A}/invoices?deleted=1`));
  });

  it.each([
    ["stale", () => stale(), /changed elsewhere/],
    ["issued meanwhile", () => alreadyIssued(), /no longer a draft/],
  ])("a refused deletion (%s) is explained and does not navigate", async (_name, answer, message) => {
    installBackend(({ method }) => (method === "DELETE" ? answer() : ok(null)));
    render(<Harness initial={invoice()} />);
    await confirm("delete-draft");
    expect(await screen.findByTestId("invoice-notice")).toHaveTextContent(message);
    expect(router.push).not.toHaveBeenCalled();
    expect(writes()).toHaveLength(1);
  });

  it("is never offered on an issued invoice", () => {
    render(<Harness initial={issued()} />);
    expect(screen.queryByTestId("delete-draft")).toBeNull();
    expect(screen.queryByText(/delete draft/i)).toBeNull();
    expectOnlyTheDownload();
  });
});

describe("who gets controls", () => {
  it("a reader sees the draft, a note, and not a single control that changes something", () => {
    render(<Harness initial={invoice()} canMutate={false} />);
    expect(screen.getByTestId("no-mutation")).toBeInTheDocument();
    for (const control of ["issue", "delete-draft", "edit-details"]) expect(screen.queryByTestId(control)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("a user whose role may mutate gets the draft controls", () => {
    render(<Harness initial={invoice()} canMutate />);
    for (const control of ["issue", "delete-draft", "edit-details"]) expect(screen.getByTestId(control)).toBeInTheDocument();
  });

  it("an issued invoice has no controls that change anything, for anyone (only the PDF download)", () => {
    render(<Harness initial={issued()} canMutate />);
    expectOnlyTheDownload();
  });

  it("a reader of an issued invoice is not told about drafts: there is nothing to explain, only the document", () => {
    render(<Harness initial={issued()} canMutate={false} />);
    expect(screen.queryByTestId("no-mutation")).toBeNull();
    expect(screen.queryByText(/issue or delete a draft/)).toBeNull();
    expect(screen.getByTestId("issued-note")).toBeInTheDocument();
  });

  it("a reader's screen never sends anything on its own", async () => {
    render(<Harness initial={invoice()} canMutate={false} />);
    await userEvent.click(screen.getByTestId("invoice-document"));
    expect(writes()).toEqual([]);
  });
});

describe("late responses and organization switching", () => {
  it("a deletion that finishes after the screen was left does not navigate", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "DELETE" ? answer.promise : ok(null)));
    const { unmount } = render(<Harness initial={invoice()} />);
    await confirm("delete-draft");
    unmount();
    await act(async () => answer.resolve(ok(null, 204)));
    expect(router.push).not.toHaveBeenCalled();
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("a deletion started in one organization cannot navigate or refresh the screen of another", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "DELETE" ? answer.promise : ok(null)));
    const { rerender } = render(<Harness key={ORG_A} initial={invoice()} orgId={ORG_A} />);
    await confirm("delete-draft");

    rerender(<Harness key={ORG_B} initial={invoice({ customer_name: "Other organization's customer" })} orgId={ORG_B} />);
    await act(async () => answer.resolve(ok(null, 204)));

    expect(router.push).not.toHaveBeenCalled();
    expect(router.refresh).not.toHaveBeenCalled();
    expect(screen.getByTestId("invoice-view")).toBeInTheDocument();
  });

  it("an issuance answer that arrives after the switch does not change the other organization's screen", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "POST" ? answer.promise : ok(null)));
    const { rerender } = render(<Harness key={ORG_A} initial={invoice()} orgId={ORG_A} />);
    await confirm("issue");

    rerender(<Harness key={ORG_B} initial={invoice({ description: "B's draft" })} orgId={ORG_B} />);
    await act(async () => answer.resolve(ok(issued({ description: "A's issued" }))));

    expect(text("invoice-description")).toBe("B's draft");
    expect(text("invoice-status")).toBe("Draft");
    expect(screen.queryByText("A's issued")).toBeNull();
    expect(screen.queryByTestId("invoice-notice")).toBeNull();
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("an unknown-outcome check that answers after the screen was left changes nothing", async () => {
    const check = deferred<ReturnType<typeof ok>>();
    installBackend(({ method, path }) => {
      if (method === "POST") return network();
      if (method === "GET" && path === INVOICE_PATH) return check.promise;
      return ok(null);
    });
    const { unmount } = render(<Harness initial={invoice()} />);
    await confirm("issue");
    await waitFor(() => expect(calls().some((call) => call.method === "GET")).toBe(true));
    unmount();
    await act(async () => check.resolve(ok(issued())));
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("an open editor and its draft do not follow the user into another organization", async () => {
    const { rerender } = render(<Harness key={ORG_A} initial={invoice()} orgId={ORG_A} />);
    await openEditor();
    type("Description", "A's unsaved draft");

    rerender(<Harness key={ORG_B} initial={invoice({ description: "B's text" })} orgId={ORG_B} />);

    expect(screen.queryByLabelText("Description")).toBeNull();
    expect(text("invoice-description")).toBe("B's text");
  });

  it("requests go to the organization of the screen they were made on", async () => {
    installBackend(({ method }) => (method === "PATCH" ? ok(invoice({ version: 4 })) : ok(null)));
    render(<Harness initial={invoice()} orgId={ORG_B} />);
    await openEditor();
    type("Description", "x");
    await userEvent.click(screen.getByTestId("save-details"));
    expect(writes()[0].orgId).toBe(ORG_B);
  });
});

describe("refreshing when the tab comes back", () => {
  it("refreshes a tab with nothing open", () => {
    render(<Harness initial={invoice()} />);
    visible();
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("never refreshes over an open editor", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    type("Description", "typing");
    visible();
    expect(router.refresh).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Description")).toHaveValue("typing");
  });

  it("refreshes again once the editor is closed", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    await userEvent.click(screen.getByTestId("cancel-details"));
    visible();
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("does not refresh while a change is running", async () => {
    const answer = deferred<ReturnType<typeof ok>>();
    installBackend(({ method }) => (method === "POST" ? answer.promise : ok(null)));
    render(<Harness initial={invoice()} />);
    await confirm("issue");
    visible();
    expect(router.refresh).not.toHaveBeenCalled();
    await act(async () => answer.resolve(ok(issued())));
  });

  it("a stale tab picks up an invoice issued elsewhere when it becomes visible", async () => {
    render(<Harness initial={invoice()} />);
    server.invoice = issued();
    visible();
    await waitFor(() => expect(text("invoice-status")).toBe("Issued"));
    expectOnlyTheDownload();
  });
});

describe("Issue and Delete wait for an open editor", () => {
  it("are disabled while the details editor is open, with a hint", async () => {
    render(<Harness initial={invoice()} />);
    await openEditor();
    expect(screen.getByTestId("issue")).toBeDisabled();
    expect(screen.getByTestId("delete-draft")).toBeDisabled();
    expect(screen.getByTestId("actions-hint")).toBeInTheDocument();
  });
});

describe("type check of the fixtures", () => {
  it("builds invoices the way the API does", () => {
    const sample: Invoice = invoice();
    expect(sample.status).toBe("draft");
  });
});
