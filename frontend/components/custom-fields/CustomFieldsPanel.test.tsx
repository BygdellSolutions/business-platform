import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CustomFieldsPanel, type CustomFieldsPanelProps } from "@/components/custom-fields/CustomFieldsPanel";
import { OrgScope } from "@/components/shell/org-context";
import { networkError, normalizeError, type ApiError, type ApiResult } from "@/lib/api/errors";
import type { Definition, FieldType, ValueRead } from "@/lib/custom-fields/types";

vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

/** Everything here is synthetic metadata: made-up entity type, keys and sources. */
const A = "00000000-0000-4000-8000-0000000000a1";
const B = "00000000-0000-4000-8000-0000000000b2";
const RECORD = "rrrrrrrr-rrrr-4rrr-8rrr-rrrrrrrrrrrr";

function def(key: string, field_type: FieldType, extra: Partial<Definition> = {}): Definition {
  return {
    id: `def-${key}`,
    entity_type: "thing",
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
const ref = (source: string, depends_on: string | null = null) => ({ reference: { source, depends_on, filter: depends_on ? "parent_id" : null } });
const read = (key: string, field_type: FieldType, value: unknown, extra: Partial<ValueRead> = {}): ValueRead => ({ key, label: key, field_type, value, display: null, active: null, missing: false, ...extra });

const ok = <T,>(data: T, status = 200): ApiResult<T> => ({ ok: true, status, data });
const fail = (status: number, body: unknown): ApiResult<never> => ({ ok: false, error: normalizeError(status, body) });
const unprocessable = (...errors: [path: string[], message: string][]) => fail(422, { detail: errors.map(([loc, msg]) => ({ loc: ["body", ...loc], msg, type: "x" })) });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

// The generic choices endpoint, simulated: a dataset per definition id, narrowed by depends_on_value.
interface Row {
  id: string;
  label: string;
  active?: boolean;
  parent?: string;
}
let dataset: Record<string, Row[]> = {};
const choiceCalls = () =>
  vi.mocked(apiFetch).mock.calls.filter(([, path]) => path.includes("/choices")).map(([orgId, path]) => ({ orgId, url: new URL(path, "http://x") }));

function installChoices(onCall?: (orgId: string, definitionId: string, params: URLSearchParams) => Promise<ApiResult<unknown>> | undefined) {
  vi.mocked(apiFetch).mockImplementation((async (orgId: string, path: string) => {
    const url = new URL(path, "http://x");
    const match = /\/custom-fields\/definitions\/([^/]+)\/choices$/.exec(url.pathname);
    if (!match) throw new Error(`unexpected request ${path}`);
    const custom = onCall?.(orgId, match[1], url.searchParams);
    if (custom) return custom;
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const parent = url.searchParams.get("depends_on_value");
    const rows = (dataset[match[1]] ?? []).filter((row) => row.label.toLowerCase().includes(q) && (parent === null || row.parent === parent) && row.active !== false);
    return ok(rows.map((row) => ({ id: row.id, label: row.label, active: row.active !== false })));
  }) as typeof apiFetch);
}

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  dataset = {};
  installChoices();
});

function mount(overrides: Partial<CustomFieldsPanelProps> & Pick<CustomFieldsPanelProps, "fields">, orgId = A) {
  const props: CustomFieldsPanelProps = {
    readOnly: false,
    save: vi.fn(async () => ok(null)),
    ...overrides,
  };
  const view = render(
    <OrgScope orgId={orgId}>
      <CustomFieldsPanel {...props} />
    </OrgScope>,
  );
  return { ...view, props, rerenderFor: (next: Partial<CustomFieldsPanelProps>, org = orgId) => view.rerender(<OrgScope orgId={org}><CustomFieldsPanel {...props} {...next} /></OrgScope>) };
}

const fieldsOf = (definitions: Definition[], values: ValueRead[] = []) => ({ entityType: "thing", entityId: RECORD, definitions, values });
const edit = () => userEvent.click(screen.getByTestId("edit-fields"));
const save = () => userEvent.click(screen.getByTestId("save-fields"));
const sent = (props: { save: unknown }) => vi.mocked(props.save as (values: unknown) => unknown).mock.calls.map(([values]) => values);
const picker = (key: string) => within(screen.getByTestId(`picker-${key}`));

const ALL_SIX = [
  def("note", "text", { position: 1 }),
  def("quantity", "number", { position: 2 }),
  def("due", "date", { position: 3 }),
  def("urgent", "boolean", { position: 4 }),
  def("size", "select", { position: 5, options: [{ id: "o1", label: "Small", position: 1, enabled: true }, { id: "o2", label: "Large", position: 2, enabled: true }, { id: "o3", label: "Retired", position: 3, enabled: false }] }),
  def("client", "reference", { position: 6, ...ref("alpha") }),
];

describe("what is rendered", () => {
  it("shows a field for each of the six types, in the order the metadata gives", async () => {
    mount({ fields: fieldsOf(ALL_SIX) });
    await edit();

    const form = screen.getByTestId("custom-fields-form");
    expect(within(form).getByLabelText("Note")).toHaveAttribute("type", "text");
    expect(within(form).getByLabelText("Quantity")).toHaveAttribute("inputmode", "decimal");
    expect(within(form).getByLabelText("Quantity")).toHaveAttribute("type", "text"); // never type=number
    expect(within(form).getByLabelText("Due")).toHaveAttribute("type", "date");
    expect(within(form).getByLabelText("Urgent").tagName).toBe("SELECT");
    expect(within(form).getByLabelText("Size").tagName).toBe("SELECT");
    expect(within(form).getByRole("combobox", { name: "Client" })).toBeInTheDocument();
    const order = Array.from(form.querySelectorAll("label")).map((label) => label.textContent);
    expect(order.slice(0, 6)).toEqual(["Note", "Quantity", "Due", "Urgent", "Size", "Client"]);
  });

  it("leaves out disabled fields and fields not meant for forms, and renders nothing without any", () => {
    mount({ fields: fieldsOf([def("a", "text"), def("b", "text", { enabled: false }), def("c", "text", { show_in_form: false })]) });
    expect(screen.getByTestId("cf-a")).toBeInTheDocument();
    expect(screen.queryByTestId("cf-b")).toBeNull();
    expect(screen.queryByTestId("cf-c")).toBeNull();
  });

  it("renders nothing at all when no field is meant for the form", () => {
    const { container } = mount({ fields: fieldsOf([def("c", "text", { show_in_form: false })]) });
    expect(container).toBeEmptyDOMElement();
  });

  it("shows saved values read-only first, with 'Not set' for what has no value", () => {
    mount({ fields: fieldsOf(ALL_SIX, [read("note", "text", "hello"), read("quantity", "number", "0.10"), read("urgent", "boolean", false)]) });
    expect(screen.getByTestId("cf-note")).toHaveTextContent("Hello".length ? "hello" : "");
    expect(screen.getByTestId("cf-quantity")).toHaveTextContent("0.10");
    expect(screen.getByTestId("cf-urgent")).toHaveTextContent("No"); // false is a value
    expect(screen.getByTestId("cf-due")).toHaveTextContent("Not set");
    expect(screen.getByTestId("cf-client")).toHaveTextContent("Not set");
  });
});

describe("required is a marker, not a rule", () => {
  const required = [def("note", "text", { required: true }), def("client", "reference", { required: true, ...ref("alpha") }), def("size", "select", { required: true, options: [] }), def("plain", "text")];

  it("marks required fields in the read-only list and in the form, for sight and for assistive technology", async () => {
    mount({ fields: fieldsOf(required) });
    expect(within(screen.getByTestId("cf-note")).getByText("Note")).toHaveAttribute("data-required", "true");
    expect(within(screen.getByTestId("cf-plain")).getByText("Plain")).not.toHaveAttribute("data-required");

    await edit();
    expect(screen.getByLabelText("Note")).toHaveAttribute("aria-required", "true");
    expect(screen.getByRole("combobox", { name: "Client" })).toHaveAttribute("aria-required", "true");
    expect(screen.getByLabelText("Size")).toHaveAttribute("aria-required", "true");
    expect(screen.getByLabelText("Plain")).not.toHaveAttribute("aria-required");
  });

  it("does not stop a save locally: a blank required field is for the backend to judge", async () => {
    const { props } = mount({ fields: fieldsOf(required, [read("note", "text", "x")]) });
    await edit();
    await userEvent.clear(screen.getByLabelText("Note"));
    await save();
    expect(sent(props)).toEqual([{ note: null }]); // sent; the backend answers 422 "Note is required"
  });

  it("shows the backend's answer for it at that control", async () => {
    mount({ fields: fieldsOf(required, [read("note", "text", "x")]), save: vi.fn(async () => unprocessable([["values", "note"], "Note is required"])) });
    await edit();
    await userEvent.clear(screen.getByLabelText("Note"));
    await save();
    expect(await screen.findByTestId("error-note")).toHaveTextContent("Note is required");
    expect(screen.getByLabelText("Note")).toHaveAttribute("aria-invalid", "true");
  });
});

describe("text, number and date keep their types", () => {
  it("saves changed text, and nothing else", async () => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX, [read("note", "text", "old"), read("quantity", "number", "1")]) });
    await edit();
    await userEvent.clear(screen.getByLabelText("Note"));
    await userEvent.type(screen.getByLabelText("Note"), "new");
    await save();
    expect(sent(props)).toEqual([{ note: "new" }]);
  });

  it.each(["0.10", "4.35", "8.20", "9999999999.99", "-12.5", "007"])("a number typed as %s is kept and sent as exactly that string", async (typed) => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX) });
    await edit();
    await userEvent.type(screen.getByLabelText("Quantity"), typed);
    expect(screen.getByLabelText("Quantity")).toHaveValue(typed);
    await save();
    const [values] = sent(props) as [Record<string, unknown>];
    expect(values.quantity).toBe(typed);
    expect(typeof values.quantity).toBe("string");
    expect(JSON.stringify(values)).toBe(`{"quantity":"${typed}"}`);
  });

  it("shows a saved number exactly as stored", () => {
    mount({ fields: fieldsOf(ALL_SIX, [read("quantity", "number", "9999999999.99")]) });
    expect(screen.getByTestId("cf-quantity")).toHaveTextContent(/^Quantity9999999999\.99$/);
  });

  it.each(["abc", "1,5", "1e2", "--1"])("a number that is not a decimal at all (%j) is stopped before any request", async (typed) => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX) });
    await edit();
    await userEvent.type(screen.getByLabelText("Quantity"), typed);
    await save();
    expect(screen.getByTestId("error-quantity")).toHaveTextContent("Enter a number such as 850.00");
    expect(sent(props)).toEqual([]);
  });

  it("a number with too many digits is the backend's to refuse, and its message shows on the control", async () => {
    const tooMany = "123456789012345678";
    const { props } = mount({ fields: fieldsOf(ALL_SIX), save: vi.fn(async () => unprocessable([["values", "quantity"], "must be a decimal with up to 14 digits and 4 decimals"])) });
    await edit();
    await userEvent.type(screen.getByLabelText("Quantity"), tooMany);
    await save();
    expect((sent(props)[0] as { quantity: string }).quantity).toBe(tooMany);
    expect(await screen.findByTestId("error-quantity")).toHaveTextContent("up to 14 digits");
  });

  it("a date is a YYYY-MM-DD string, sent as is", async () => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX) });
    await edit();
    fireEvent.change(screen.getByLabelText("Due"), { target: { value: "2026-10-03" } });
    await save();
    expect(sent(props)).toEqual([{ due: "2026-10-03" }]);
  });

  it("clearing a saved value sends null", async () => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX, [read("note", "text", "x"), read("due", "date", "2026-10-03")]) });
    await edit();
    await userEvent.clear(screen.getByLabelText("Note"));
    fireEvent.change(screen.getByLabelText("Due"), { target: { value: "" } });
    await save();
    expect(sent(props)).toEqual([{ note: null, due: null }]);
  });
});

describe("a boolean has three states", () => {
  const bool = [def("urgent", "boolean")];

  it("starts as Not set when there is no value, and offers Not set / Yes / No", async () => {
    mount({ fields: fieldsOf(bool) });
    await edit();
    const select = screen.getByLabelText("Urgent") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual(["Not set", "Yes", "No"]);
  });

  it.each([
    [[], "true", { urgent: true }],
    [[], "false", { urgent: false }], // unset -> No: a change, and it sends false (not null, not nothing)
    [[read("urgent", "boolean", true)], "false", { urgent: false }],
    [[read("urgent", "boolean", false)], "true", { urgent: true }],
    [[read("urgent", "boolean", true)], "", { urgent: null }],
    [[read("urgent", "boolean", false)], "", { urgent: null }],
  ] as [ValueRead[], string, Record<string, boolean | null>][])("%# changing to %j sends %j", async (saved, choose, expected) => {
    const { props } = mount({ fields: fieldsOf(bool, saved) });
    await edit();
    await userEvent.selectOptions(screen.getByLabelText("Urgent"), choose);
    await save();
    expect(sent(props)).toEqual([expected]);
  });

  it.each([[[]], [[read("urgent", "boolean", true)]], [[read("urgent", "boolean", false)]]] as [ValueRead[]][])("%# leaving it as it was sends nothing", async (saved) => {
    const { props } = mount({ fields: fieldsOf(bool, saved) });
    await edit();
    await save();
    expect(sent(props)).toEqual([]);
  });

  it("a saved false is shown as No (and is selected as No), never as not set", async () => {
    mount({ fields: fieldsOf(bool, [read("urgent", "boolean", false)]) });
    expect(screen.getByTestId("cf-urgent")).toHaveTextContent("No");
    await edit();
    expect((screen.getByLabelText("Urgent") as HTMLSelectElement).value).toBe("false");
  });
});

describe("a select stores the option's UUID", () => {
  it("offers the backend's enabled options and sends the id of the chosen one, not its label", async () => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX) });
    await edit();
    const select = screen.getByLabelText("Size") as HTMLSelectElement;
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual(["Not set", "Small", "Large"]); // "Retired" is disabled

    await userEvent.selectOptions(select, "Large");
    await save();

    expect(sent(props)).toEqual([{ size: "o2" }]);
    expect(JSON.stringify(sent(props))).not.toContain("Large");
  });

  it("keeps showing a chosen option that has been disabled since, without resending it", async () => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX, [read("size", "select", "o3", { display: "Retired", active: false })]) });
    await edit();
    const select = screen.getByLabelText("Size") as HTMLSelectElement;
    expect(select.value).toBe("o3");
    expect(Array.from(select.options).map((option) => option.textContent)).toContain("Retired (disabled)");
    await userEvent.type(screen.getByLabelText("Note"), "something else");
    await save();
    expect(sent(props)).toEqual([{ note: "something else" }]); // the retired option is not resent
  });

  it("Not set clears it", async () => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX, [read("size", "select", "o1", { display: "Small", active: true })]) });
    await edit();
    await userEvent.selectOptions(screen.getByLabelText("Size"), "Not set");
    await save();
    expect(sent(props)).toEqual([{ size: null }]);
  });

  it("a saved option that no longer exists is shown safely", () => {
    mount({ fields: fieldsOf(ALL_SIX, [read("size", "select", "gone", { display: null, active: null, missing: true })]) });
    expect(screen.getByTestId("cf-size")).toHaveTextContent("(no longer exists)");
  });
});

describe("a reference stores the referenced record's UUID", () => {
  const CLIENTS = [def("client", "reference", ref("alpha"))];

  it("gets its choices from the generic choices endpoint of its definition, and sends the id, not the label", async () => {
    dataset["def-client"] = [{ id: "uuid-anna", label: "Anna Andersson" }, { id: "uuid-bo", label: "Bo Berg" }];
    const { props } = mount({ fields: fieldsOf(CLIENTS) });
    await edit();

    await userEvent.click(picker("client").getByRole("combobox"));
    await userEvent.click(await picker("client").findByRole("option", { name: /Anna/ }));
    await save();

    expect(choiceCalls()[0].url.pathname).toBe("/custom-fields/definitions/def-client/choices");
    expect(choiceCalls()[0].orgId).toBe(A);
    expect(sent(props)).toEqual([{ client: "uuid-anna" }]);
    expect(JSON.stringify(sent(props))).not.toContain("Anna");
  });

  it("searches as the user types, by name", async () => {
    dataset["def-client"] = [{ id: "u1", label: "Anna Andersson" }, { id: "u2", label: "Bo Berg" }];
    mount({ fields: fieldsOf(CLIENTS) });
    await edit();
    await userEvent.type(picker("client").getByRole("combobox"), "bo b");
    await waitFor(() => expect(picker("client").getAllByRole("option")).toHaveLength(1));
    expect(choiceCalls().map(({ url }) => url.searchParams.get("q"))).toContain("bo b");
  });

  it("typing a name without choosing it assigns nothing", async () => {
    dataset["def-client"] = [{ id: "u1", label: "Anna Andersson" }];
    const { props } = mount({ fields: fieldsOf(CLIENTS) });
    await edit();
    await userEvent.type(picker("client").getByRole("combobox"), "Anna Andersson");
    await picker("client").findAllByRole("option");
    await save();
    expect(sent(props)).toEqual([]);
  });

  it("only offers what the backend returns: a deactivated target is not newly assignable", async () => {
    dataset["def-client"] = [{ id: "u1", label: "Active One" }, { id: "u2", label: "Gone Inactive", active: false }];
    mount({ fields: fieldsOf(CLIENTS) });
    await edit();
    await userEvent.click(picker("client").getByRole("combobox"));
    await picker("client").findAllByRole("option");
    expect(picker("client").queryByRole("option", { name: /Gone Inactive/ })).toBeNull();
    expect(choiceCalls().every(({ url }) => !url.searchParams.has("include_inactive"))).toBe(true);
  });

  it("an existing inactive target is still displayed, marked, kept, and not resent", async () => {
    const { props } = mount({ fields: fieldsOf([...CLIENTS, def("note", "text")], [read("client", "reference", "uuid-old", { display: "Old Client", active: false })]) });
    expect(screen.getByTestId("cf-client")).toHaveTextContent("Old Client");
    expect(screen.getByTestId("cf-client")).toHaveTextContent("(inactive)");

    await edit();
    expect(picker("client").getByRole("combobox")).toHaveValue("Old Client (inactive)");
    await userEvent.type(screen.getByLabelText("Note"), "x");
    await save();
    expect(sent(props)).toEqual([{ note: "x" }]);
  });

  it("a target that no longer exists is shown safely, never crashes, and is not replaced", async () => {
    const { props } = mount({ fields: fieldsOf([...CLIENTS, def("note", "text")], [read("client", "reference", "uuid-gone", { display: null, active: null, missing: true })]) });
    expect(screen.getByTestId("cf-client")).toHaveTextContent("(no longer exists)");
    expect(screen.getByTestId("cf-client")).not.toHaveTextContent("(inactive)");

    await edit();
    expect(picker("client").getByRole("combobox")).toHaveValue("(no longer exists)");
    await userEvent.type(screen.getByLabelText("Note"), "x");
    await save();
    expect(sent(props)).toEqual([{ note: "x" }]); // nothing is substituted for the missing target
  });

  it("clearing it sends null", async () => {
    const { props } = mount({ fields: fieldsOf(CLIENTS, [read("client", "reference", "uuid-anna", { display: "Anna", active: true })]) });
    await edit();
    await userEvent.click(screen.getByRole("button", { name: "Clear Client" }));
    await save();
    expect(sent(props)).toEqual([{ client: null }]);
  });

  it("two choices with the same label are different values", async () => {
    dataset["def-client"] = [{ id: "u1", label: "Same Name" }, { id: "u2", label: "Same Name" }];
    const { props } = mount({ fields: fieldsOf(CLIENTS) });
    await edit();
    await userEvent.click(picker("client").getByRole("combobox"));
    const options = await picker("client").findAllByRole("option");
    await userEvent.click(options[1]);
    await save();
    expect(sent(props)).toEqual([{ client: "u2" }]);
  });
});

describe("read-only", () => {
  it("shows every value, offers no editing, and has no inputs at all", () => {
    mount({ readOnly: true, fields: fieldsOf(ALL_SIX, [read("note", "text", "n"), read("urgent", "boolean", false), read("client", "reference", "u", { display: "Anna", active: true })]) });
    expect(screen.queryByTestId("edit-fields")).toBeNull();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("combobox")).toHaveLength(0);
    expect(screen.getByTestId("cf-note")).toHaveTextContent("n");
    expect(screen.getByTestId("cf-urgent")).toHaveTextContent("No");
    expect(screen.getByTestId("cf-client")).toHaveTextContent("Anna");
  });

  it("an open form disappears when the record stops being editable, and does not return when it becomes editable again", async () => {
    const { rerenderFor } = mount({ fields: fieldsOf(ALL_SIX) });
    await edit();
    expect(screen.getByTestId("custom-fields-form")).toBeInTheDocument();

    rerenderFor({ readOnly: true });
    expect(screen.queryByTestId("custom-fields-form")).toBeNull();
    rerenderFor({ readOnly: false });
    expect(screen.queryByTestId("custom-fields-form")).toBeNull();
    expect(screen.getByTestId("edit-fields")).toBeInTheDocument();
  });

  it("is disabled while something else is running", () => {
    mount({ busy: true, fields: fieldsOf(ALL_SIX) });
    expect(screen.getByTestId("edit-fields")).toBeDisabled();
  });
});

describe("the backend's answers", () => {
  it("maps each 422 to its own control, keeps the draft, and keeps messages that have no control", async () => {
    const answer = unprocessable([["values", "note"], "must be text of 1 to 2000 characters"], [["values", "due"], "must be a real calendar date written YYYY-MM-DD"], [["values", "nonsense"], "Unknown field"], [["somewhere"], "Something general"]);
    mount({ fields: fieldsOf(ALL_SIX), save: vi.fn(async () => answer) });
    await edit();
    await userEvent.type(screen.getByLabelText("Note"), "draft text");

    await save();

    expect(await screen.findByTestId("error-note")).toHaveTextContent("1 to 2000 characters");
    expect(screen.getByTestId("error-due")).toHaveTextContent("real calendar date");
    expect(screen.queryByTestId("error-quantity")).toBeNull();
    expect(screen.getByTestId("form-error")).toHaveTextContent("values.nonsense: Unknown field");
    expect(screen.getByTestId("form-error")).toHaveTextContent("somewhere: Something general");
    expect(screen.getByLabelText("Note")).toHaveValue("draft text");
  });

  it("passes everything else (locked record, missing record, network, server) on, and keeps the draft to retry", async () => {
    const failures: [ApiResult<never>, ApiError["kind"]][] = [
      [fail(409, { detail: "This record is locked; its custom fields cannot be changed" }), "conflict"],
      [fail(404, { detail: "Not found" }), "not_found"],
      [fail(500, { detail: "Traceback" }), "server"],
      [{ ok: false, error: networkError() }, "network"],
      [fail(403, { detail: "Your role does not allow this." }), "forbidden"],
    ];
    for (const [answer, kind] of failures) {
      const onFailure = vi.fn();
      const { unmount } = mount({ fields: fieldsOf(ALL_SIX), save: vi.fn(async () => answer), onFailure });
      await edit();
      await userEvent.type(screen.getByLabelText("Note"), "keep me");

      await save();

      await waitFor(() => expect(onFailure).toHaveBeenCalledTimes(1));
      expect(onFailure.mock.calls[0][0].kind).toBe(kind);
      expect(screen.getByLabelText("Note")).toHaveValue("keep me");
      expect(screen.getByTestId("save-fields")).toBeEnabled(); // can be retried
      unmount();
    }
  });

  it("shows messages from outside at the control, in the read-only list and in the form", async () => {
    mount({ fields: fieldsOf(ALL_SIX), externalErrors: { quantity: ["Quantity is required"] } });
    expect(within(screen.getByTestId("cf-quantity")).getByTestId("error-quantity")).toHaveTextContent("Quantity is required");

    await edit();
    expect(screen.getByTestId("error-quantity")).toHaveTextContent("Quantity is required");
    expect(screen.getByLabelText("Quantity")).toHaveAttribute("aria-invalid", "true");
  });

  it("makes no request when nothing changed, and does nothing if the caller says 'not now'", async () => {
    const { props } = mount({ fields: fieldsOf(ALL_SIX) });
    await edit();
    await save();
    expect(sent(props)).toEqual([]);
    expect(screen.queryByTestId("custom-fields-form")).toBeNull();

    const busyProps = { fields: fieldsOf(ALL_SIX), save: vi.fn(async () => null) };
    mount(busyProps);
    await userEvent.click(screen.getAllByTestId("edit-fields")[1]);
    await userEvent.type(screen.getByLabelText("Note"), "x");
    await save();
    expect(screen.getByTestId("custom-fields-form")).toBeInTheDocument(); // still open, nothing lost
  });

  it("tells the page while the form is open", async () => {
    const closed = vi.fn();
    const registerEditor = vi.fn(() => closed);
    mount({ fields: fieldsOf(ALL_SIX), registerEditor });
    expect(registerEditor).not.toHaveBeenCalled();

    await edit();
    expect(registerEditor).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByTestId("cancel-fields"));
    expect(closed).toHaveBeenCalledTimes(1);
  });
});

describe("dependencies, from metadata only", () => {
  // Parent -> child: a made-up pair. Nothing in the code under test knows these names.
  const PAIR = [def("region", "reference", { position: 1, ...ref("source_one") }), def("district", "reference", { position: 2, ...ref("source_two", "region") })];

  function seedPair() {
    dataset["def-region"] = [{ id: "r-north", label: "North" }, { id: "r-south", label: "South" }];
    dataset["def-district"] = [
      { id: "d-1", label: "Harbour", parent: "r-north" },
      { id: "d-2", label: "Hill", parent: "r-north" },
      { id: "d-3", label: "Lake", parent: "r-south" },
    ];
  }
  async function choose(key: string, name: RegExp) {
    await userEvent.click(picker(key).getByRole("combobox"));
    await userEvent.click(await picker(key).findByRole("option", { name }));
  }

  it("keeps the dependent field disabled, saying what to choose first, until the parent has a value", async () => {
    seedPair();
    mount({ fields: fieldsOf(PAIR) });
    await edit();

    expect(picker("district").getByRole("combobox")).toBeDisabled();
    expect(screen.getByText("Choose Region first.")).toBeInTheDocument();
    await userEvent.click(picker("district").getByRole("combobox"));
    expect(choiceCalls().filter(({ url }) => url.pathname.includes("def-district"))).toHaveLength(0); // not even asked
  });

  it("asks for the dependent choices with the parent's UUID, and shows only what the backend returns", async () => {
    seedPair();
    mount({ fields: fieldsOf(PAIR) });
    await edit();

    await choose("region", /North/);
    expect(picker("district").getByRole("combobox")).toBeEnabled();
    await userEvent.click(picker("district").getByRole("combobox"));
    await picker("district").findAllByRole("option");

    const asked = choiceCalls().filter(({ url }) => url.pathname.includes("def-district"));
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every(({ url }) => url.searchParams.get("depends_on_value") === "r-north")).toBe(true);
    expect(picker("district").getAllByRole("option").map((option) => option.textContent)).toEqual(["Harbour", "Hill"]); // not Lake
  });

  it("changing the parent clears the dependent field at once, and one request carries both", async () => {
    seedPair();
    const { props } = mount({ fields: fieldsOf(PAIR, [read("region", "reference", "r-north", { display: "North", active: true }), read("district", "reference", "d-1", { display: "Harbour", active: true })]) });
    await edit();
    expect(picker("district").getByRole("combobox")).toHaveValue("Harbour");

    await choose("region", /South/);

    expect(picker("district").getByRole("combobox")).toHaveValue(""); // cleared immediately
    await save();
    expect(sent(props)).toEqual([{ region: "r-south", district: null }]); // ONE request, both changes
  });

  it("clearing the parent clears the dependent field and disables it again", async () => {
    seedPair();
    const { props } = mount({ fields: fieldsOf(PAIR, [read("region", "reference", "r-north", { display: "North", active: true }), read("district", "reference", "d-1", { display: "Harbour", active: true })]) });
    await edit();

    await userEvent.click(screen.getByRole("button", { name: "Clear Region" }));

    expect(picker("district").getByRole("combobox")).toHaveValue("");
    expect(picker("district").getByRole("combobox")).toBeDisabled();
    await save();
    expect(sent(props)).toEqual([{ region: null, district: null }]);
  });

  it("choosing the same parent again does not clear anything it should not (no change, no request)", async () => {
    seedPair();
    const { props } = mount({ fields: fieldsOf(PAIR, [read("region", "reference", "r-north", { display: "North", active: true }), read("district", "reference", "d-1", { display: "Harbour", active: true })]) });
    await edit();
    await save();
    expect(sent(props)).toEqual([]);
  });

  it("a child changed on its own sends only the child", async () => {
    seedPair();
    const { props } = mount({ fields: fieldsOf(PAIR, [read("region", "reference", "r-north", { display: "North", active: true }), read("district", "reference", "d-1", { display: "Harbour", active: true })]) });
    await edit();
    await choose("district", /Hill/);
    await save();
    expect(sent(props)).toEqual([{ district: "d-2" }]);
  });

  it("shows the backend's dependency refusal on the field it is about, keeping the draft", async () => {
    seedPair();
    mount({ fields: fieldsOf(PAIR), save: vi.fn(async () => unprocessable([["values", "district"], "This is not one of the choices for the selected Region"])) });
    await edit();
    await choose("region", /North/);
    await choose("district", /Harbour/);

    await save();

    expect(await screen.findByTestId("error-district")).toHaveTextContent("not one of the choices for the selected Region");
    expect(screen.queryByTestId("error-region")).toBeNull();
    expect(picker("district").getByRole("combobox")).toHaveValue("Harbour");
  });

  describe("a chain of any length: Customer -> Project -> Work Order -> Task (made-up sources)", () => {
    const CHAIN = [
      def("customer", "reference", { position: 1, ...ref("source_a") }),
      def("project", "reference", { position: 2, ...ref("source_b", "customer") }),
      def("workorder", "reference", { position: 3, ...ref("source_c", "project") }),
      def("task", "reference", { position: 4, ...ref("source_d", "workorder") }),
      def("note", "text", { position: 5 }),
    ];
    const filled = [
      read("customer", "reference", "c1", { display: "Customer One", active: true }),
      read("project", "reference", "p1", { display: "Project One", active: true }),
      read("workorder", "reference", "w1", { display: "Work Order One", active: true }),
      read("task", "reference", "t1", { display: "Task One", active: true }),
    ];
    beforeEach(() => {
      dataset["def-customer"] = [{ id: "c1", label: "Customer One" }, { id: "c2", label: "Customer Two" }];
      dataset["def-project"] = [{ id: "p1", label: "Project One", parent: "c1" }, { id: "p2", label: "Project Two", parent: "c2" }];
      dataset["def-workorder"] = [{ id: "w1", label: "Work Order One", parent: "p1" }, { id: "w2", label: "Work Order Two", parent: "p2" }];
      dataset["def-task"] = [{ id: "t1", label: "Task One", parent: "w1" }];
    });

    it("changing the top clears every level below it, in one request", async () => {
      const { props } = mount({ fields: fieldsOf(CHAIN, filled) });
      await edit();

      await choose("customer", /Customer Two/);

      for (const key of ["project", "workorder", "task"]) expect(picker(key).getByRole("combobox")).toHaveValue("");
      expect(picker("project").getByRole("combobox")).toBeEnabled(); // its parent has a value again
      expect(picker("workorder").getByRole("combobox")).toBeDisabled();
      expect(picker("task").getByRole("combobox")).toBeDisabled();
      await save();
      expect(sent(props)).toEqual([{ customer: "c2", project: null, workorder: null, task: null }]);
    });

    it("changing a middle level clears only what is below it", async () => {
      const { props } = mount({ fields: fieldsOf(CHAIN, filled) });
      await edit();

      await userEvent.click(screen.getByRole("button", { name: "Clear Project" }));

      expect(picker("customer").getByRole("combobox")).toHaveValue("Customer One");
      for (const key of ["workorder", "task"]) expect(picker(key).getByRole("combobox")).toHaveValue("");
      await save();
      expect(sent(props)).toEqual([{ project: null, workorder: null, task: null }]);
    });

    it("clearing the top clears the whole chain", async () => {
      const { props } = mount({ fields: fieldsOf(CHAIN, filled) });
      await edit();
      await userEvent.click(screen.getByRole("button", { name: "Clear Customer" }));
      for (const key of ["customer", "project", "workorder", "task"]) expect(picker(key).getByRole("combobox")).toHaveValue("");
      await save();
      expect(sent(props)).toEqual([{ customer: null, project: null, workorder: null, task: null }]);
    });

    it("narrows each level by ITS parent's value", async () => {
      mount({ fields: fieldsOf(CHAIN, filled.slice(0, 1)) });
      await edit();
      await choose("project", /Project One/);
      await choose("workorder", /Work Order One/);
      await userEvent.click(picker("task").getByRole("combobox"));
      await picker("task").findAllByRole("option");

      const dependsOn = (key: string) => choiceCalls().filter(({ url }) => url.pathname.includes(`def-${key}`)).map(({ url }) => url.searchParams.get("depends_on_value"));
      expect(new Set(dependsOn("project"))).toEqual(new Set(["c1"]));
      expect(new Set(dependsOn("workorder"))).toEqual(new Set(["p1"]));
      expect(new Set(dependsOn("task"))).toEqual(new Set(["w1"]));
    });
  });
});

describe("stale choice responses", () => {
  const PAIR = [def("region", "reference", { position: 1, ...ref("s1") }), def("district", "reference", { position: 2, ...ref("s2", "region") })];

  it("a slow answer for the OLD parent never fills the dependent field after the parent changed", async () => {
    dataset["def-region"] = [{ id: "r-a", label: "Region A" }, { id: "r-b", label: "Region B" }];
    const slowForA = deferred<ApiResult<unknown>>();
    installChoices((_org, definitionId, params) => (definitionId === "def-district" && params.get("depends_on_value") === "r-a" ? slowForA.promise : undefined));
    dataset["def-district"] = [{ id: "d-b", label: "District of B", parent: "r-b" }, { id: "d-a", label: "District of A", parent: "r-a" }];
    mount({ fields: fieldsOf(PAIR) });
    await edit();

    await userEvent.click(picker("region").getByRole("combobox"));
    await userEvent.click(await picker("region").findByRole("option", { name: /Region A/ }));
    await userEvent.click(picker("district").getByRole("combobox")); // asks for A's districts; no answer yet
    await userEvent.click(picker("region").getByRole("combobox")); // moves on: the district list closes
    await userEvent.click(await picker("region").findByRole("option", { name: /Region B/ }));
    await userEvent.click(picker("district").getByRole("combobox"));
    expect(await picker("district").findAllByRole("option")).toHaveLength(1);

    await act(async () => slowForA.resolve(ok([{ id: "d-a", label: "District of A", active: true }]))); // A's answer finally arrives

    expect(picker("district").getAllByRole("option").map((option) => option.textContent)).toEqual(["District of B"]);
    expect(document.body.textContent).not.toContain("District of A");
  });

  it("a parent change while the dependent list is open drops its choices at once", async () => {
    dataset["def-region"] = [{ id: "r-a", label: "Region A" }];
    dataset["def-district"] = [{ id: "d-a", label: "District of A", parent: "r-a" }];
    const { rerenderFor, props } = mount({ fields: fieldsOf(PAIR, [read("region", "reference", "r-a", { display: "Region A", active: true })]) });
    await edit();
    await userEvent.click(picker("district").getByRole("combobox"));
    expect(await picker("district").findAllByRole("option")).toHaveLength(1);
    void rerenderFor;
    void props;

    await userEvent.click(screen.getByRole("button", { name: "Clear Region" })); // the parent is cleared (focus leaves the dependent list)

    expect(picker("district").getByRole("combobox")).toBeDisabled();
    expect(picker("district").queryByRole("option")).toBeNull();
  });
});

describe("organization scope", () => {
  const CLIENT = [def("client", "reference", ref("alpha"))];

  it("a draft and chosen values do not exist after the organization changes", async () => {
    dataset["def-client"] = [{ id: "u1", label: "Anna" }];
    const { rerenderFor } = mount({ fields: fieldsOf([...CLIENT, def("note", "text")]) }, A);
    await edit();
    await userEvent.type(screen.getByLabelText("Note"), "typed in A");
    await userEvent.click(picker("client").getByRole("combobox"));
    await userEvent.click(await picker("client").findByRole("option"));

    rerenderFor({ fields: fieldsOf([...CLIENT, def("note", "text")]) }, B);

    expect(screen.queryByTestId("custom-fields-form")).toBeNull();
    expect(document.body.textContent).not.toContain("typed in A");
    expect(document.body.textContent).not.toContain("Anna");
  });

  it("choices are asked for the organization the panel is shown for", async () => {
    dataset["def-client"] = [{ id: "u1", label: "Anna" }];
    const { rerenderFor } = mount({ fields: fieldsOf(CLIENT) }, A);
    rerenderFor({}, B);
    await edit();
    await userEvent.click(picker("client").getByRole("combobox"));
    await picker("client").findAllByRole("option");
    expect(new Set(choiceCalls().map((call) => call.orgId))).toEqual(new Set([B]));
  });

  it("a slow answer for organization A never fills the picker shown for organization B", async () => {
    const slowForA = deferred<ApiResult<unknown>>();
    installChoices((org) => (org === A ? slowForA.promise : undefined));
    dataset["def-client"] = [{ id: "ub", label: "Choice in B" }];
    const { rerenderFor } = mount({ fields: fieldsOf(CLIENT) }, A);
    await edit();
    await userEvent.click(picker("client").getByRole("combobox")); // asks A, no answer yet

    rerenderFor({ fields: fieldsOf(CLIENT) }, B);
    await edit();
    await userEvent.click(picker("client").getByRole("combobox"));
    expect(await picker("client").findAllByRole("option")).toHaveLength(1);
    await act(async () => slowForA.resolve(ok([{ id: "ua", label: "Choice in A", active: true }])));

    expect(picker("client").getAllByRole("option").map((option) => option.textContent)).toEqual(["Choice in B"]);
    expect(document.body.textContent).not.toContain("Choice in A");
  });
});
