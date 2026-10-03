import { describe, expect, it } from "vitest";

import {
  MISSING_TARGET,
  NOT_A_DATE,
  applyChange,
  blankDraft,
  buildChanges,
  descendantsOf,
  fieldKeyOfError,
  formDefinitions,
  initialDrafts,
  isUnset,
  parentOf,
  parentValueId,
  shownValue,
  type Draft,
  type Drafts,
} from "@/lib/custom-fields/model";
import type { Definition, FieldType, ValueRead } from "@/lib/custom-fields/types";
import { NOT_A_DECIMAL } from "@/lib/forms";

/** Metadata only. The sources below are made-up names: the code under test cannot know them. */
function def(key: string, field_type: FieldType, extra: Partial<Definition> = {}): Definition {
  return {
    id: `id-${key}`,
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
const ref = (source: string, depends_on: string | null = null): Pick<Definition, "reference"> => ({ reference: { source, depends_on, filter: depends_on ? "parent_id" : null } });

function read(key: string, field_type: FieldType, value: unknown, extra: Partial<ValueRead> = {}): ValueRead {
  return { key, label: key, field_type, value, display: null, active: null, missing: false, ...extra };
}

const ALL_SIX = [
  def("note", "text", { position: 1 }),
  def("quantity", "number", { position: 2 }),
  def("due", "date", { position: 3 }),
  def("urgent", "boolean", { position: 4 }),
  def("size", "select", { position: 5, options: [{ id: "o1", label: "Small", position: 1, enabled: true }, { id: "o2", label: "Large", position: 2, enabled: false }] }),
  def("client", "reference", { position: 6, ...ref("alpha") }),
];

/** A chain of any length, from metadata alone: Customer -> Project -> Work Order -> Task. */
const CHAIN = [def("customer", "reference", ref("alpha")), def("project", "reference", ref("beta", "customer")), def("workorder", "reference", ref("gamma", "project")), def("task", "reference", ref("delta", "workorder")), def("note", "text")];

const pick = (id: string, label: string, inactive = false) => ({ id, label, inactive });
const asRef = (id: string, label: string): Draft => ({ type: "reference", value: pick(id, label) });

describe("formDefinitions", () => {
  it("keeps the backend's order and drops disabled fields and fields not meant for forms", () => {
    const list = [def("a", "text"), def("b", "text", { enabled: false }), def("c", "text", { show_in_form: false }), def("d", "text")];
    expect(formDefinitions(list).map((d) => d.key)).toEqual(["a", "d"]);
  });
});

describe("initialDrafts: one draft per field, blank where nothing is saved", () => {
  it("is blank in the right way for each of the six types", () => {
    const drafts = initialDrafts(ALL_SIX, []);
    expect(drafts).toEqual({
      note: { type: "text", value: "" },
      quantity: { type: "number", value: "" },
      due: { type: "date", value: "" },
      urgent: { type: "boolean", value: null },
      size: { type: "select", value: null },
      client: { type: "reference", value: null },
    });
  });

  it("holds each saved value in its own type", () => {
    const drafts = initialDrafts(ALL_SIX, [
      read("note", "text", "hello"),
      read("quantity", "number", "12.5"),
      read("due", "date", "2026-10-03"),
      read("urgent", "boolean", true),
      read("size", "select", "o1", { display: "Small", active: true }),
      read("client", "reference", "u1", { display: "Anna", active: true }),
    ]);
    expect(drafts.note).toEqual({ type: "text", value: "hello" });
    expect(drafts.quantity).toEqual({ type: "number", value: "12.5" });
    expect(drafts.due).toEqual({ type: "date", value: "2026-10-03" });
    expect(drafts.urgent).toEqual({ type: "boolean", value: true });
    expect(drafts.size).toEqual({ type: "select", value: pick("o1", "Small") });
    expect(drafts.client).toEqual({ type: "reference", value: pick("u1", "Anna") });
  });

  it.each(["0.10", "4.35", "8.20", "9999999999.99", "-1234.5678", "100", "0", "12.5"])("a saved number %s stays the same string", (text) => {
    const draft = initialDrafts([def("quantity", "number")], [read("quantity", "number", text)]).quantity;
    expect(draft).toEqual({ type: "number", value: text });
    expect(typeof draft.value).toBe("string");
  });

  it("keeps boolean false (a value) apart from no value", () => {
    const [absent, saved] = [initialDrafts([def("urgent", "boolean")], []), initialDrafts([def("urgent", "boolean")], [read("urgent", "boolean", false)])];
    expect(absent.urgent).toEqual({ type: "boolean", value: null });
    expect(saved.urgent).toEqual({ type: "boolean", value: false });
    expect(isUnset(absent.urgent)).toBe(true);
    expect(isUnset(saved.urgent)).toBe(false); // false is NOT unset
  });

  it("shows a deactivated option or reference target as what it is, without losing its id", () => {
    const drafts = initialDrafts(ALL_SIX, [read("size", "select", "o2", { display: "Large", active: false }), read("client", "reference", "u9", { display: "Old", active: false })]);
    expect(drafts.size).toEqual({ type: "select", value: pick("o2", "Large", true) });
    expect(drafts.client).toEqual({ type: "reference", value: pick("u9", "Old", true) });
  });

  it("shows a target that no longer exists safely, and still remembers its id", () => {
    const drafts = initialDrafts(ALL_SIX, [read("client", "reference", "gone", { display: null, active: null, missing: true })]);
    expect(drafts.client).toEqual({ type: "reference", value: { id: "gone", label: MISSING_TARGET, inactive: false } });
  });

  it("does not invent drafts for fields that are not on the form", () => {
    expect(Object.keys(initialDrafts([def("a", "text"), def("b", "text", { show_in_form: false })], []))).toEqual(["a"]);
  });
});

describe("isUnset", () => {
  it.each([
    [{ type: "text", value: "" }, true],
    [{ type: "text", value: "   " }, true],
    [{ type: "text", value: "x" }, false],
    [{ type: "number", value: "" }, true],
    [{ type: "number", value: "0" }, false],
    [{ type: "date", value: "" }, true],
    [{ type: "boolean", value: null }, true],
    [{ type: "boolean", value: true }, false],
    [{ type: "boolean", value: false }, false],
    [{ type: "select", value: null }, true],
    [{ type: "reference", value: null }, true],
    [{ type: "reference", value: { id: "x", label: "", inactive: false } }, false],
  ] as [Draft, boolean][])("%j is unset: %s", (draft, expected) => {
    expect(isUnset(draft)).toBe(expected);
  });
});

describe("dependencies are read from metadata, to any depth", () => {
  it("finds the parent a definition names", () => {
    expect(parentOf(CHAIN, CHAIN[2])?.key).toBe("project");
    expect(parentOf(CHAIN, CHAIN[0])).toBeUndefined();
    expect(parentOf(CHAIN, def("orphan", "reference", ref("x", "nowhere")))).toBeUndefined();
  });

  it("lists every descendant through any number of levels, nearest first", () => {
    expect(descendantsOf(CHAIN, "customer")).toEqual(["project", "workorder", "task"]);
    expect(descendantsOf(CHAIN, "project")).toEqual(["workorder", "task"]);
    expect(descendantsOf(CHAIN, "task")).toEqual([]);
    expect(descendantsOf(CHAIN, "note")).toEqual([]);
  });

  it("handles branches and a malformed cycle without looping", () => {
    const branched = [def("root", "reference", ref("a")), def("left", "reference", ref("b", "root")), def("right", "reference", ref("c", "root")), def("leaf", "reference", ref("d", "left"))];
    expect(descendantsOf(branched, "root").sort()).toEqual(["leaf", "left", "right"]);
    const cycle = [def("a", "reference", ref("x", "b")), def("b", "reference", ref("y", "a"))];
    expect(descendantsOf(cycle, "a")).toEqual(["b"]);
  });

  const filled: Drafts = {
    customer: asRef("c1", "Customer One"),
    project: asRef("p1", "Project One"),
    workorder: asRef("w1", "Work Order One"),
    task: asRef("t1", "Task One"),
    note: { type: "text", value: "keep me" },
  };

  it("changing a field clears everything below it, recursively, and nothing else", () => {
    const next = applyChange(CHAIN, filled, "customer", asRef("c2", "Customer Two"));
    expect(next.customer).toEqual(asRef("c2", "Customer Two"));
    expect(next.project).toEqual({ type: "reference", value: null });
    expect(next.workorder).toEqual({ type: "reference", value: null });
    expect(next.task).toEqual({ type: "reference", value: null });
    expect(next.note).toEqual({ type: "text", value: "keep me" });
  });

  it("changing a middle field clears only what is below it", () => {
    const next = applyChange(CHAIN, filled, "project", asRef("p2", "Project Two"));
    expect(next.customer).toEqual(filled.customer);
    expect(next.project).toEqual(asRef("p2", "Project Two"));
    expect(next.workorder).toEqual({ type: "reference", value: null });
    expect(next.task).toEqual({ type: "reference", value: null });
  });

  it("clearing the top clears all of them", () => {
    const next = applyChange(CHAIN, filled, "customer", { type: "reference", value: null });
    expect(["customer", "project", "workorder", "task"].every((key) => isUnset(next[key]))).toBe(true);
    expect(next.note).toEqual(filled.note);
  });

  it("does not change the drafts it was given", () => {
    const before = structuredClone(filled);
    applyChange(CHAIN, filled, "customer", asRef("c2", "x"));
    expect(filled).toEqual(before);
  });

  it("also works for a dependency between a select and another type, from metadata alone", () => {
    const defs = [def("kind", "select", { options: [] }), def("subkind", "reference", ref("whatever", "kind"))];
    const next = applyChange(defs, { kind: { type: "select", value: pick("o1", "A") }, subkind: asRef("s1", "S") }, "kind", { type: "select", value: pick("o2", "B") });
    expect(next.subkind).toEqual({ type: "reference", value: null });
  });

  it("tells a field which value its choices must be narrowed by", () => {
    expect(parentValueId(CHAIN, filled, CHAIN[1])).toBe("c1");
    expect(parentValueId(CHAIN, filled, CHAIN[3])).toBe("w1");
    expect(parentValueId(CHAIN, filled, CHAIN[0])).toBeNull(); // no parent
    expect(parentValueId(CHAIN, { ...filled, customer: { type: "reference", value: null } }, CHAIN[1])).toBeNull(); // parent unset
  });
});

describe("buildChanges: only what changed, one request, strings stay strings", () => {
  const base = initialDrafts(ALL_SIX, [
    read("note", "text", "old"),
    read("quantity", "number", "12.5"),
    read("due", "date", "2026-10-03"),
    read("urgent", "boolean", true),
    read("size", "select", "o1", { display: "Small", active: true }),
    read("client", "reference", "u1", { display: "Anna", active: true }),
  ]);

  it("sends nothing when nothing changed", () => {
    expect(buildChanges(ALL_SIX, base, structuredClone(base))).toEqual({ values: {}, errors: {} });
  });

  it("sends only the changed fields, keyed by field key", () => {
    const drafts = { ...base, note: { type: "text", value: "new" } as Draft, size: { type: "select", value: pick("o2", "Large") } as Draft };
    expect(buildChanges(ALL_SIX, base, drafts).values).toEqual({ note: "new", size: "o2" });
  });

  it("sends the UUID of a select or reference, never its label", () => {
    const drafts = { ...base, client: asRef("u2", "Some Label"), size: { type: "select", value: pick("o2", "Large") } as Draft };
    const { values } = buildChanges(ALL_SIX, base, drafts);
    expect(values.client).toBe("u2");
    expect(JSON.stringify(values)).not.toContain("Some Label");
    expect(JSON.stringify(values)).not.toContain("Large");
  });

  it.each(["0.10", "4.35", "8.20", "9999999999.99", "-0.5", "007"])("a number typed as %s is sent as exactly that string", (typed) => {
    const { values } = buildChanges(ALL_SIX, base, { ...base, quantity: { type: "number", value: typed } });
    expect(values.quantity).toBe(typed);
    expect(typeof values.quantity).toBe("string");
  });

  it("trims a number or text, and a blank one clears the value (null)", () => {
    expect(buildChanges(ALL_SIX, base, { ...base, quantity: { type: "number", value: "  7.25 " }, note: { type: "text", value: "  padded  " } }).values).toEqual({ quantity: "7.25", note: "padded" });
    expect(buildChanges(ALL_SIX, base, { ...base, quantity: { type: "number", value: "" }, note: { type: "text", value: "   " } }).values).toEqual({ quantity: null, note: null });
  });

  it("a blank field that was already blank is not a change", () => {
    const empty = initialDrafts(ALL_SIX, []);
    expect(buildChanges(ALL_SIX, empty, structuredClone(empty))).toEqual({ values: {}, errors: {} });
  });

  describe("booleans have three states", () => {
    const bool = [def("urgent", "boolean")];
    const state = (value: boolean | null): Drafts => ({ urgent: { type: "boolean", value } });

    it.each([
      [null, true, true],
      [null, false, false], // unset -> false is a CHANGE, and sends false (not null)
      [true, false, false],
      [false, true, true],
      [true, null, null],
      [false, null, null],
    ] as [boolean | null, boolean | null, boolean | null][])("%s -> %s sends %s", (from, to, sent) => {
      const { values } = buildChanges(bool, state(from), state(to));
      expect(values).toHaveProperty("urgent");
      expect(values.urgent).toBe(sent);
    });

    it.each([null, true, false])("%s -> the same is no change", (value) => {
      expect(buildChanges(bool, state(value), state(value)).values).toEqual({});
    });
  });

  it("stops a number that is not a decimal at all and a date that is not a date, before any request", () => {
    for (const typed of ["abc", "1,5", "1e2", "--1", "1.", ".5"]) {
      const { values, errors } = buildChanges(ALL_SIX, base, { ...base, quantity: { type: "number", value: typed } });
      expect(values).toEqual({});
      expect(errors.quantity).toEqual([NOT_A_DECIMAL]);
    }
    for (const typed of ["2026-1-3", "tomorrow", "03/10/2026"]) {
      expect(buildChanges(ALL_SIX, base, { ...base, due: { type: "date", value: typed } }).errors.due).toEqual([NOT_A_DATE]);
    }
  });

  it("leaves limits to the backend: too many digits, an impossible date and a long text are sent as typed", () => {
    const { values, errors } = buildChanges(ALL_SIX, base, {
      ...base,
      quantity: { type: "number", value: "123456789012345678.123456" },
      due: { type: "date", value: "2026-02-31" },
      note: { type: "text", value: "x".repeat(5000) },
    });
    expect(errors).toEqual({});
    expect(values.quantity).toBe("123456789012345678.123456");
    expect(values.due).toBe("2026-02-31");
    expect((values.note as string).length).toBe(5000);
  });

  it("sends the cleared dependents in the SAME request as the parent change", () => {
    const filled: Drafts = { customer: asRef("c1", "C"), project: asRef("p1", "P"), workorder: asRef("w1", "W"), task: asRef("t1", "T"), note: { type: "text", value: "" } };
    const next = applyChange(CHAIN, filled, "customer", asRef("c2", "C2"));

    expect(buildChanges(CHAIN, filled, next).values).toEqual({ customer: "c2", project: null, workorder: null, task: null });
  });

  it("does not send a dependent that was already blank", () => {
    const partly: Drafts = { customer: asRef("c1", "C"), project: { type: "reference", value: null }, workorder: { type: "reference", value: null }, task: { type: "reference", value: null }, note: { type: "text", value: "" } };
    const next = applyChange(CHAIN, partly, "customer", asRef("c2", "C2"));
    expect(buildChanges(CHAIN, partly, next).values).toEqual({ customer: "c2" });
  });

  it("a deactivated reference that is not touched is not resent", () => {
    const withInactive = initialDrafts(ALL_SIX, [read("client", "reference", "u9", { display: "Old", active: false })]);
    const { values } = buildChanges(ALL_SIX, withInactive, { ...withInactive, note: { type: "text", value: "changed" } });
    expect(values).toEqual({ note: "changed" });
  });

  it("returns the changes in form order", () => {
    const drafts = { ...base, client: asRef("u2", "x"), note: { type: "text", value: "z" } as Draft };
    expect(Object.keys(buildChanges(ALL_SIX, base, drafts).values)).toEqual(["note", "client"]);
  });
});

describe("shownValue (read-only)", () => {
  it("shows nothing set as unset, and false as No", () => {
    expect(shownValue(def("urgent", "boolean"), undefined)).toEqual({ kind: "unset" });
    expect(shownValue(def("urgent", "boolean"), read("urgent", "boolean", false))).toEqual({ kind: "boolean", text: "No" });
    expect(shownValue(def("urgent", "boolean"), read("urgent", "boolean", true))).toEqual({ kind: "boolean", text: "Yes" });
  });

  it("shows each type's saved value as the backend sent it", () => {
    expect(shownValue(def("note", "text"), read("note", "text", "hi"))).toEqual({ kind: "text", text: "hi" });
    expect(shownValue(def("due", "date"), read("due", "date", "2026-10-03"))).toEqual({ kind: "text", text: "2026-10-03" });
    expect(shownValue(def("quantity", "number"), read("quantity", "number", "0.10"))).toEqual({ kind: "number", text: "0.10" });
  });

  it("shows a select or reference by its display text, flagging an inactive or missing target", () => {
    expect(shownValue(def("client", "reference"), read("client", "reference", "u1", { display: "Anna", active: true }))).toEqual({ kind: "target", text: "Anna", inactive: false, missing: false });
    expect(shownValue(def("client", "reference"), read("client", "reference", "u1", { display: "Old", active: false }))).toEqual({ kind: "target", text: "Old", inactive: true, missing: false });
    expect(shownValue(def("client", "reference"), read("client", "reference", "u1", { display: null, active: null, missing: true }))).toEqual({ kind: "target", text: MISSING_TARGET, inactive: false, missing: true });
  });
});

describe("fieldKeyOfError", () => {
  it("takes the field key from a values-write error location", () => {
    expect(fieldKeyOfError("values.owner")).toBe("owner");
    expect(fieldKeyOfError("values.some_key")).toBe("some_key");
    expect(fieldKeyOfError("values")).toBeNull();
    expect(fieldKeyOfError("name")).toBeNull();
  });
});

describe("blankDraft", () => {
  it("is unset for every type", () => {
    for (const definition of ALL_SIX) expect(isUnset(blankDraft(definition))).toBe(true);
  });
});
