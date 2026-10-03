import { isDateShape } from "@/lib/dates";
import { parseCustomNumber } from "@/lib/decimal";
import { NOT_A_DECIMAL } from "@/lib/forms";
import type { Definition, ValueRead } from "@/lib/custom-fields/types";

/**
 * The pure rules of the Custom Fields form: what a draft looks like for each of the six types,
 * which values changed, what clears what. Everything is driven by Definition metadata; nothing
 * here knows what any field is FOR.
 *
 * Typed semantics are kept exactly:
 *   text       text. A blank text is "no value".
 *   number     a decimal STRING, compared and sent as the string typed. Never a JavaScript number.
 *   date       `YYYY-MM-DD`
 *   boolean    THREE states: unset (null), true, false. `false` is a value, not an absence.
 *   select     the option's UUID
 *   reference  the referenced record's UUID
 */

export interface RefDraft {
  id: string;
  label: string;
  /** The target is no longer selectable for a new assignment (still displayed). */
  inactive: boolean;
}

export type Draft =
  | { type: "text"; value: string }
  | { type: "number"; value: string }
  | { type: "date"; value: string }
  | { type: "boolean"; value: boolean | null }
  | { type: "select"; value: RefDraft | null }
  | { type: "reference"; value: RefDraft | null };

/** The draft of every field of a record, by field key. */
export type Drafts = Record<string, Draft>;

export const NOT_A_DATE = "Enter a date such as 2026-10-03.";
export const MISSING_TARGET = "(no longer exists)";

/** Fields the form shows: enabled, and flagged for forms by the backend. */
export function formDefinitions(definitions: Definition[]): Definition[] {
  return definitions.filter((definition) => definition.enabled && definition.show_in_form);
}

export function blankDraft(definition: Definition): Draft {
  switch (definition.field_type) {
    case "text":
      return { type: "text", value: "" };
    case "number":
      return { type: "number", value: "" };
    case "date":
      return { type: "date", value: "" };
    case "boolean":
      return { type: "boolean", value: null };
    case "select":
      return { type: "select", value: null };
    case "reference":
      return { type: "reference", value: null };
  }
}

function draftFromValue(definition: Definition, read: ValueRead): Draft {
  const raw = read.value;
  switch (definition.field_type) {
    case "text":
      return { type: "text", value: typeof raw === "string" ? raw : "" };
    case "number":
      // The backend sends a decimal string. It is kept as that string, character for character.
      return { type: "number", value: typeof raw === "string" ? raw : String(raw) };
    case "date":
      return { type: "date", value: typeof raw === "string" ? raw : "" };
    case "boolean":
      return { type: "boolean", value: typeof raw === "boolean" ? raw : null };
    case "select":
    case "reference": {
      const id = typeof raw === "string" ? raw : "";
      const target: RefDraft = { id, label: read.missing ? MISSING_TARGET : (read.display ?? ""), inactive: read.active === false };
      return definition.field_type === "select" ? { type: "select", value: id ? target : null } : { type: "reference", value: id ? target : null };
    }
  }
}

/** One draft per form field: the saved value where there is one, otherwise blank. */
export function initialDrafts(definitions: Definition[], values: ValueRead[]): Drafts {
  const byKey = new Map(values.map((read) => [read.key, read]));
  const drafts: Drafts = {};
  for (const definition of formDefinitions(definitions)) {
    const read = byKey.get(definition.key);
    drafts[definition.key] = read === undefined ? blankDraft(definition) : draftFromValue(definition, read);
  }
  return drafts;
}

export function isUnset(draft: Draft): boolean {
  switch (draft.type) {
    case "text":
    case "number":
    case "date":
      return draft.value.trim() === "";
    case "boolean":
    case "select":
    case "reference":
      return draft.value === null;
  }
}

// --- dependencies, from metadata only ---------------------------------------------------------------------------------

/** The field this one depends on, if the metadata names one that is on the form. */
export function parentOf(definitions: Definition[], definition: Definition): Definition | undefined {
  const parentKey = definition.reference?.depends_on;
  return parentKey ? definitions.find((candidate) => candidate.key === parentKey) : undefined;
}

/** Every field that depends on `key`, directly or through others (a chain of any length). */
export function descendantsOf(definitions: Definition[], key: string): string[] {
  const found: string[] = [];
  const queue = [key];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const definition of definitions) {
      if (definition.reference?.depends_on === current && definition.key !== key && !found.includes(definition.key)) {
        found.push(definition.key);
        queue.push(definition.key);
      }
    }
  }
  return found;
}

/**
 * Set one field. Whenever a field changes, everything that depends on it, through any number of
 * levels, is cleared: a child chosen for the old parent is not a valid choice for the new one.
 * (Clearing a descendant that is already blank changes nothing.)
 */
export function applyChange(definitions: Definition[], drafts: Drafts, key: string, next: Draft): Drafts {
  const result: Drafts = { ...drafts, [key]: next };
  for (const descendant of descendantsOf(definitions, key)) {
    const definition = definitions.find((candidate) => candidate.key === descendant);
    if (definition && descendant in result) result[descendant] = blankDraft(definition);
  }
  return result;
}

/** The id the choices of `definition` must be narrowed by: its parent's current value, or null. */
export function parentValueId(definitions: Definition[], drafts: Drafts, definition: Definition): string | null {
  const parent = parentOf(definitions, definition);
  if (!parent) return null;
  const draft = drafts[parent.key];
  if (!draft || (draft.type !== "select" && draft.type !== "reference")) return null;
  return draft.value?.id ?? null;
}

// --- what to send ------------------------------------------------------------------------------------------------------

/** The value as it would travel (null = no value), compared as-is: strings stay strings. */
function wire(draft: Draft): string | boolean | null {
  switch (draft.type) {
    case "text":
    case "number":
      return draft.value.trim() === "" ? null : draft.value.trim();
    case "date":
      return draft.value === "" ? null : draft.value;
    case "boolean":
      return draft.value;
    case "select":
    case "reference":
      return draft.value === null ? null : draft.value.id;
  }
}

export interface Changes {
  /** `{key: value or null}` for the fields that differ from what was saved. Null clears. */
  values: Record<string, string | boolean | null>;
  /** Shape problems that stop the request (not a decimal at all, not a date). The backend judges the rest. */
  errors: Record<string, string[]>;
}

/**
 * The fields whose value differs from `base`, in form order, ready for ONE request. Cleared
 * dependents travel in the same request as the parent change: the backend checks a record's
 * values together, and a new parent saved next to an old child would be refused.
 */
export function buildChanges(definitions: Definition[], base: Drafts, drafts: Drafts): Changes {
  const values: Changes["values"] = {};
  const errors: Changes["errors"] = {};
  for (const definition of formDefinitions(definitions)) {
    const draft = drafts[definition.key];
    const before = base[definition.key];
    if (!draft || !before || wire(draft) === wire(before)) continue;
    const sent = wire(draft);
    if (sent !== null && draft.type === "number" && parseCustomNumber(sent as string) === null) {
      errors[definition.key] = [NOT_A_DECIMAL];
      continue;
    }
    if (sent !== null && draft.type === "date" && !isDateShape(sent as string)) {
      errors[definition.key] = [NOT_A_DATE];
      continue;
    }
    values[definition.key] = sent;
  }
  return { values, errors };
}

// --- showing a saved value ----------------------------------------------------------------------------------------------

export type Shown =
  | { kind: "unset" }
  | { kind: "text"; text: string }
  | { kind: "number"; text: string }
  | { kind: "boolean"; text: "Yes" | "No" }
  | { kind: "target"; text: string; inactive: boolean; missing: boolean };

/** What to print for a value in a read-only view. Absence is "unset"; `false` is "No". */
export function shownValue(definition: Definition, read: ValueRead | undefined): Shown {
  if (read === undefined) return { kind: "unset" };
  switch (definition.field_type) {
    case "text":
    case "date":
      return { kind: "text", text: typeof read.value === "string" ? read.value : "" };
    case "number":
      return { kind: "number", text: typeof read.value === "string" ? read.value : String(read.value) };
    case "boolean":
      return typeof read.value === "boolean" ? { kind: "boolean", text: read.value ? "Yes" : "No" } : { kind: "unset" };
    case "select":
    case "reference":
      return { kind: "target", text: read.missing ? MISSING_TARGET : (read.display ?? ""), inactive: read.active === false, missing: read.missing };
  }
}

/** Map a 422 location of a values write (`values.<key>`) to the field key. */
export function fieldKeyOfError(path: string): string | null {
  return path.startsWith("values.") ? path.slice("values.".length) : null;
}
