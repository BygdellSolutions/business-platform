import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Harness, LINE_1, LINE_2, NO_FIELDS, ORG_A, ORG_B, TX_ID, fail, installBackend, line, ok, resetServer, router, second, server, tx, writes } from "@/features/transactions/testing";
import type { TransactionFields } from "@/features/transactions/editor-context";
import { networkError, type ApiResult } from "@/lib/api/errors";
import type { Definition, FieldType, ValueRead } from "@/lib/custom-fields/types";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

function def(key: string, field_type: FieldType, extra: Partial<Definition> = {}): Definition {
  return {
    id: `def-${key}`,
    entity_type: "x",
    key,
    label: key[0].toUpperCase() + key.slice(1),
    field_type,
    required: false,
    position: 0,
    enabled: true,
    show_in_form: true,
    show_in_table: false,
    show_on_invoice: false,
    reference: null,
    options: null,
    created_at: "",
    updated_at: "",
    ...extra,
  };
}
const read = (key: string, field_type: FieldType, value: unknown, extra: Partial<ValueRead> = {}): ValueRead => ({ key, label: key, field_type, value, display: null, active: null, missing: false, ...extra });
const ref = (source: string, depends_on: string | null = null) => ({ reference: { source, depends_on, filter: depends_on ? "parent_id" : null } });

// Generic definitions; nothing here is specific to any business.
const TX_DEFS = [def("project", "text", { position: 1, required: true }), def("priority", "boolean", { position: 2 })];
const LINE_DEFS = [def("region", "reference", { position: 1, required: true, ...ref("s1") }), def("district", "reference", { position: 2, required: true, ...ref("s2", "region") })];

function fieldsWith(overrides: { transactionValues?: ValueRead[]; lineValues?: Record<string, ValueRead[]>; txDefs?: Definition[]; lineDefs?: Definition[] } = {}): TransactionFields {
  return {
    transaction: { definitions: overrides.txDefs ?? TX_DEFS, values: overrides.transactionValues ?? [] },
    line: { definitions: overrides.lineDefs ?? LINE_DEFS, values: overrides.lineValues ?? {} },
  };
}

const transactionPanel = () => within(document.getElementById("transaction-fields")!);
const linePanel = (id: string) => within(document.getElementById(`line-${id}-fields`)!);

function problem(entity_type: string, entity_id: string, field: string, label: string) {
  return { code: "custom_field.required", message: `${label} is required`, entity_type, entity_id, field, label };
}
const blocked = (...problems: ReturnType<typeof problem>[]) =>
  fail(409, { detail: { code: "validation_failed", event: "complete", message: `The complete step was blocked: ${problems.length} problem(s) must be fixed first`, total: problems.length, problems } });

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(tx());
  installBackend(() => ok(null));
});

describe("where the fields appear", () => {
  it("the transaction's own fields are shown with the transaction, and each line's fields with that line", () => {
    render(
      <Harness
        initial={tx()}
        fields={fieldsWith({
          transactionValues: [read("project", "text", "Spring"), read("priority", "boolean", false)],
          lineValues: { [LINE_1]: [read("region", "reference", "r1", { display: "North", active: true })], [LINE_2]: [read("region", "reference", "r2", { display: "South", active: true })] },
        })}
      />,
    );

    expect(transactionPanel().getByText("Spring")).toBeInTheDocument();
    expect(transactionPanel().getByTestId("cf-priority")).toHaveTextContent("No"); // false, not "not set"
    expect(linePanel(LINE_1).getByTestId("cf-region")).toHaveTextContent("North");
    expect(linePanel(LINE_2).getByTestId("cf-region")).toHaveTextContent("South");
    expect(linePanel(LINE_1).getByTestId("cf-district")).toHaveTextContent("Not set");
    expect(screen.getAllByTestId("line-fields-row")).toHaveLength(2);
  });

  it("renders nothing for a record type without custom fields", () => {
    render(<Harness initial={tx()} fields={fieldsWith({ txDefs: [], lineDefs: [] })} />);
    expect(screen.queryByTestId("custom-fields")).toBeNull();
    expect(screen.queryByTestId("line-fields-row")).toBeNull();
  });

  it("works with only line fields, or only transaction fields", () => {
    const { unmount } = render(<Harness initial={tx()} fields={fieldsWith({ txDefs: [] })} />);
    expect(document.getElementById("transaction-fields")).toBeNull();
    expect(screen.getAllByTestId("line-fields-row")).toHaveLength(2);
    unmount();
    render(<Harness initial={tx()} fields={fieldsWith({ lineDefs: [] })} />);
    expect(document.getElementById("transaction-fields")).not.toBeNull();
    expect(screen.queryByTestId("line-fields-row")).toBeNull();
  });

  it("a completed or cancelled transaction shows the values read-only: no Edit, no inputs", () => {
    for (const status of ["completed", "cancelled"] as const) {
      const { unmount } = render(<Harness initial={tx({ status })} fields={fieldsWith({ transactionValues: [read("project", "text", "Spring")], lineValues: { [LINE_1]: [read("region", "reference", "r1", { display: "North", active: true })] } })} />);
      expect(screen.queryByTestId("edit-fields")).toBeNull();
      expect(transactionPanel().getByText("Spring")).toBeInTheDocument();
      expect(linePanel(LINE_1).getByTestId("cf-region")).toHaveTextContent("North");
      expect(screen.queryAllByRole("combobox")).toHaveLength(0);
      unmount();
    }
  });

  it("a deactivated reference and a missing one are shown on a read-only record, without crashing", () => {
    render(
      <Harness
        initial={tx({ status: "completed" })}
        fields={fieldsWith({ lineValues: { [LINE_1]: [read("region", "reference", "r1", { display: "Old Region", active: false })], [LINE_2]: [read("region", "reference", "r2", { display: null, active: null, missing: true })] } })}
      />,
    );
    expect(linePanel(LINE_1).getByTestId("cf-region")).toHaveTextContent("Old Region");
    expect(linePanel(LINE_1).getByTestId("cf-region")).toHaveTextContent("(inactive)");
    expect(linePanel(LINE_2).getByTestId("cf-region")).toHaveTextContent("(no longer exists)");
  });
});

describe("saving values", () => {
  it("saves a line's fields to that line's own values endpoint, with NO Sales version, then refreshes the page", async () => {
    render(<Harness initial={tx({ version: 4, lines: [line({ version: 7 }), second()] })} fields={fieldsWith({ lineDefs: [def("note", "text")] })} />);

    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));
    await userEvent.type(linePanel(LINE_1).getByLabelText("Note"), "hello");
    await userEvent.click(linePanel(LINE_1).getByTestId("save-fields"));

    expect(writes()).toEqual([{ method: "PATCH", path: `/custom-fields/entities/transaction_line/${LINE_1}/values`, body: { values: { note: "hello" } }, ifMatch: undefined, orgId: ORG_A }]);
    expect(router.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByTestId("custom-fields-form")).toBeNull());
  });

  it("saves the transaction's fields to the transaction's values endpoint, with no Sales version", async () => {
    render(<Harness initial={tx()} fields={fieldsWith({ lineDefs: [] })} />);
    await userEvent.click(transactionPanel().getByTestId("edit-fields"));
    await userEvent.type(transactionPanel().getByLabelText("Project"), "Autumn");
    await userEvent.selectOptions(transactionPanel().getByLabelText("Priority"), "false");
    await userEvent.click(transactionPanel().getByTestId("save-fields"));

    expect(writes()).toEqual([{ method: "PATCH", path: `/custom-fields/entities/transaction/${TX_ID}/values`, body: { values: { project: "Autumn", priority: false } }, ifMatch: undefined, orgId: ORG_A }]);
  });

  it("never touches a Sales endpoint, so no Sales version is used or moved", async () => {
    render(<Harness initial={tx()} fields={fieldsWith({ lineDefs: [def("note", "text")] })} />);
    await userEvent.click(linePanel(LINE_2).getByTestId("edit-fields"));
    await userEvent.type(linePanel(LINE_2).getByLabelText("Note"), "x");
    await userEvent.click(linePanel(LINE_2).getByTestId("save-fields"));
    expect(writes().every((call) => call.path.startsWith("/custom-fields/") && call.ifMatch === undefined)).toBe(true);
  });

  it("a dependent line field sends the parent change and the cleared child in ONE request", async () => {
    installBackend((call) => {
      if (call.path.includes("/choices")) return ok([{ id: "r-new", label: "New Region", active: true }]);
      return ok(null);
    });
    render(<Harness initial={tx()} fields={fieldsWith({ lineValues: { [LINE_1]: [read("region", "reference", "r-old", { display: "Old Region", active: true }), read("district", "reference", "d-old", { display: "Old District", active: true })] } })} />);

    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));
    await userEvent.click(within(screen.getByTestId("picker-region")).getByRole("combobox"));
    await userEvent.click(await within(screen.getByTestId("picker-region")).findByRole("option", { name: /New Region/ }));
    expect(within(screen.getByTestId("picker-district")).getByRole("combobox")).toHaveValue("");
    await userEvent.click(linePanel(LINE_1).getByTestId("save-fields"));

    expect(writes()).toHaveLength(1);
    expect(writes()[0].body).toEqual({ values: { region: "r-new", district: null } });
  });

  it("choices come from the generic choices endpoint of the definition, for this organization", async () => {
    installBackend((call) => (call.path.includes("/choices") ? ok([]) : ok(null)));
    render(<Harness initial={tx()} fields={fieldsWith()} />);
    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));
    await userEvent.click(within(screen.getByTestId("picker-region")).getByRole("combobox"));
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls.some(([, path]) => path.startsWith("/custom-fields/definitions/def-region/choices"))).toBe(true));
    const choiceCalls = vi.mocked(apiFetch).mock.calls.filter(([, path]) => path.includes("/choices"));
    expect(choiceCalls.every(([orgId]) => orgId === ORG_A)).toBe(true);
    expect(vi.mocked(apiFetch).mock.calls.some(([, path]) => path.startsWith("/customers") || path.startsWith("/horses"))).toBe(false); // no other module is asked
  });

  it("one change at a time: the Edit buttons wait while a save runs, and the lifecycle waits while a form is open", async () => {
    let finish!: (value: ApiResult<unknown>) => void;
    installBackend(() => new Promise((resolve) => (finish = resolve)));
    render(<Harness initial={tx()} fields={fieldsWith({ lineDefs: [def("note", "text")] })} />);
    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));
    expect(screen.getByTestId("complete")).toBeDisabled(); // a fields form is an open editor
    expect(screen.getByTestId("lifecycle-hint")).toBeInTheDocument();
    await userEvent.type(linePanel(LINE_1).getByLabelText("Note"), "x");

    await userEvent.click(linePanel(LINE_1).getByTestId("save-fields"));
    expect(linePanel(LINE_2).getByTestId("edit-fields")).toBeDisabled();
    expect(screen.getByTestId("edit-header")).toBeDisabled();
    expect(writes()).toHaveLength(1);

    await act(async () => finish(ok(null)));
  });
});

describe("failures", () => {
  async function failWith(answer: ApiResult<unknown>) {
    installBackend(() => answer);
    server.tx = tx({ status: "completed", version: 9 });
    render(<Harness initial={tx()} fields={fieldsWith({ lineDefs: [def("note", "text")] })} />);
    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));
    await userEvent.type(linePanel(LINE_1).getByLabelText("Note"), "my draft");
    await userEvent.click(linePanel(LINE_1).getByTestId("save-fields"));
  }

  it("a 422 shows on the specific field, with no banner, and keeps the draft", async () => {
    installBackend(() => fail(422, { detail: [{ loc: ["body", "values", "note"], msg: "must be text of 1 to 2000 characters", type: "custom_field.invalid" }] }));
    render(<Harness initial={tx()} fields={fieldsWith({ lineDefs: [def("note", "text")] })} />);
    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));
    await userEvent.type(linePanel(LINE_1).getByLabelText("Note"), "x");
    await userEvent.click(linePanel(LINE_1).getByTestId("save-fields"));

    expect(await linePanel(LINE_1).findByTestId("error-note")).toHaveTextContent("1 to 2000");
    expect(screen.queryByTestId("editor-notice")).toBeNull();
    expect(router.refresh).not.toHaveBeenCalled();
    expect(linePanel(LINE_1).getByLabelText("Note")).toHaveValue("x");
  });

  it("a locked record (409) is explained as 'no longer a draft' and the page refreshes into its read-only state", async () => {
    await failWith(fail(409, { detail: "This record is locked; its custom fields cannot be changed" }));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("no longer a draft, so your changes could not be saved");
    expect(router.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId("tx-status")).toHaveTextContent("Completed"));
    expect(screen.queryByTestId("custom-fields-form")).toBeNull();
    expect(screen.queryByTestId("edit-fields")).toBeNull();
  });

  it("a missing record (404) says so without revealing why, and refreshes", async () => {
    await failWith(fail(404, { detail: "Not found" }));
    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("no longer exists, or you do not have access to it");
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it.each([
    [fail(500, { detail: "Traceback: secret" }), "could not complete the request"],
    [{ ok: false, error: networkError() } as ApiResult<unknown>, "Could not reach the server"],
    [fail(403, { detail: "Your role in this organization does not allow this." }), "does not allow this"],
  ])("%# is shown in the established way, keeps the draft, and can be retried", async (answer, text) => {
    await failWith(answer);
    expect(await screen.findByTestId("editor-notice")).toHaveTextContent(text);
    expect(document.body.textContent).not.toContain("secret");
    expect(linePanel(LINE_1).getByLabelText("Note")).toHaveValue("my draft");
    expect(linePanel(LINE_1).getByTestId("save-fields")).toBeEnabled();
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("an open fields form goes away when the transaction stops being a draft", async () => {
    render(<Harness initial={tx()} fields={fieldsWith()} />);
    await userEvent.click(transactionPanel().getByTestId("edit-fields"));
    expect(screen.getByTestId("custom-fields-form")).toBeInTheDocument();

    server.tx = tx({ status: "completed", version: 9 });
    await act(async () => router.refresh());

    expect(screen.queryByTestId("custom-fields-form")).toBeNull();
    expect(screen.queryByTestId("edit-fields")).toBeNull();
  });
});

describe("a blocked completion, at the controls", () => {
  const ALL = fieldsWith({ lineValues: {} });

  async function complete(answer: ApiResult<unknown>) {
    installBackend(() => answer);
    render(<Harness initial={tx()} fields={ALL} />);
    await userEvent.click(screen.getByTestId("complete"));
  }

  it("puts a transaction problem on that transaction field and a line problem on that line's field, and keeps the banner", async () => {
    await complete(blocked(problem("transaction", TX_ID, "project", "Project"), problem("transaction_line", LINE_2, "district", "District")));

    expect(await screen.findByTestId("editor-notice")).toHaveTextContent("The complete step was blocked: 2 problem(s) must be fixed first");
    // at the controls
    expect(within(transactionPanel().getByTestId("cf-project")).getByTestId("error-project")).toHaveTextContent("Project is required");
    expect(within(linePanel(LINE_2).getByTestId("cf-district")).getByTestId("error-district")).toHaveTextContent("District is required");
    // and only there: not on the other fields, not on the other line
    expect(transactionPanel().queryByTestId("error-priority")).toBeNull();
    expect(linePanel(LINE_1).queryByTestId("error-district")).toBeNull();
    expect(linePanel(LINE_2).queryByTestId("error-region")).toBeNull();
    // the summary list is preserved, with a link to each place
    const items = within(screen.getByTestId("editor-problems")).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["Transaction · Project: Project is required", "Line 2 · District: District is required"]);
    expect(within(items[0]).getByRole("link")).toHaveAttribute("href", "#transaction-fields");
    expect(within(items[1]).getByRole("link")).toHaveAttribute("href", `#line-${LINE_2}-fields`);
    expect(document.getElementById("transaction-fields")).not.toBeNull();
    expect(document.getElementById(`line-${LINE_2}-fields`)).not.toBeNull();
  });

  it("also shows the message at the control while the fields are being edited", async () => {
    await complete(blocked(problem("transaction_line", LINE_1, "region", "Region")));
    await screen.findByTestId("editor-notice");

    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));

    expect(linePanel(LINE_1).getByTestId("error-region")).toHaveTextContent("Region is required");
    expect(within(screen.getByTestId("picker-region")).getByRole("combobox")).toHaveAttribute("aria-invalid", "true");
  });

  it("a problem about something that is not on a form is kept in the banner only", async () => {
    await complete(blocked(problem("transaction_line", LINE_1, "hidden_field", "Hidden"), problem("transaction_line", "not-a-line-on-this-page", "region", "Region")));
    const items = within(await screen.findByTestId("editor-problems")).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["Line 1 · Hidden: Hidden is required", "Record · Region: Region is required"]);
    expect(within(items[1]).queryByRole("link")).toBeNull(); // no place to link to
    expect(screen.queryAllByTestId("error-region")).toHaveLength(0);
  });

  it("the errors go away when the user saves values, and completion then succeeds with none shown", async () => {
    let completions = 0;
    installBackend((call) => {
      if (call.path.endsWith("/complete")) return ++completions === 1 ? blocked(problem("transaction", TX_ID, "project", "Project")) : ok(tx({ status: "completed" }));
      return ok(null);
    });
    render(<Harness initial={tx()} fields={fieldsWith({ lineDefs: [] })} />);
    await userEvent.click(screen.getByTestId("complete"));
    expect(await transactionPanel().findByTestId("error-project")).toBeInTheDocument();

    await userEvent.click(transactionPanel().getByTestId("edit-fields"));
    await userEvent.type(transactionPanel().getByLabelText("Project"), "Supplied");
    await userEvent.click(transactionPanel().getByTestId("save-fields"));
    await waitFor(() => expect(screen.queryByTestId("custom-fields-form")).toBeNull());
    expect(transactionPanel().queryByTestId("error-project")).toBeNull(); // cleared by the save
    expect(screen.queryByTestId("editor-problems")).toBeNull();

    server.tx = tx({ status: "completed", version: 9 });
    await userEvent.click(screen.getByTestId("complete"));
    await waitFor(() => expect(screen.getByTestId("tx-status")).toHaveTextContent("Completed"));
    expect(screen.queryByTestId("editor-notice")).toBeNull();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("the completion itself is the backend's decision: the screen never completes or refuses on its own", async () => {
    installBackend(() => ok(tx({ status: "completed" })));
    render(<Harness initial={tx()} fields={fieldsWith({ transactionValues: [], lineValues: {} })} />);
    await userEvent.click(screen.getByTestId("complete")); // required fields are empty, and the request is still sent
    expect(writes()).toHaveLength(1);
    expect(writes()[0].path).toBe(`/transactions/${TX_ID}/complete`);
  });
});

describe("organization scope", () => {
  it("a fields draft typed in one organization does not exist in another", async () => {
    const { rerender } = render(<Harness initial={tx()} orgId={ORG_A} fields={fieldsWith({ lineDefs: [def("note", "text")] })} />);
    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));
    await userEvent.type(linePanel(LINE_1).getByLabelText("Note"), "typed in A");

    rerender(<Harness initial={tx()} orgId={ORG_B} fields={NO_FIELDS} />);

    expect(screen.queryByTestId("custom-fields-form")).toBeNull();
    expect(document.body.textContent).not.toContain("typed in A");
  });

  it("a save that is still running when the user switches organization is ignored: no refresh, no notice", async () => {
    let finish!: (value: ApiResult<unknown>) => void;
    installBackend(() => new Promise((resolve) => (finish = resolve)));
    const { rerender } = render(<Harness initial={tx()} orgId={ORG_A} fields={fieldsWith({ lineDefs: [def("note", "text")] })} />);
    await userEvent.click(linePanel(LINE_1).getByTestId("edit-fields"));
    await userEvent.type(linePanel(LINE_1).getByLabelText("Note"), "x");
    await userEvent.click(linePanel(LINE_1).getByTestId("save-fields"));

    rerender(<Harness initial={tx({ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" })} orgId={ORG_B} fields={NO_FIELDS} />);
    await act(async () => finish(fail(409, { detail: "This record is locked; its custom fields cannot be changed" })));

    expect(router.refresh).not.toHaveBeenCalled();
    expect(screen.queryByTestId("editor-notice")).toBeNull();
  });
});

