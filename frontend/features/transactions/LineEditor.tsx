"use client";

import { useEffect, useMemo, useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { DecimalText } from "@/components/ui/DecimalText";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { EntityPicker, type PickerEntity } from "@/components/ui/EntityPicker";
import { DecimalField, TextField } from "@/components/ui/Field";
import { itemSearch } from "@/features/catalog/item-picker";
import { Notice } from "@/components/ui/Notice";
import { useEditor, useRegisterEditor } from "@/features/transactions/editor-context";
import { classify, TEXT } from "@/features/transactions/failures";
import { SUBJECT_KINDS } from "@/features/transactions/service-subjects";
import { apiFetch } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/errors";
import type { Colleague, LineUpdate, TransactionLine } from "@/lib/api/types";
import { parseMoney, parsePercent, parseQuantity } from "@/lib/decimal";
import { NOT_A_DECIMAL, NO_PROBLEMS, problemsFrom, type Problems } from "@/lib/forms";

const CONTROLS = [
  "item_id",
  "description",
  "unit",
  "quantity",
  "unit_price_ex_vat",
  "vat_rate",
  "line_discount_percent",
  "subject_id",
  "performed_by_user_id",
  "performed_at",
  "notes",
] as const;

type Kind = "item" | "service" | "adhoc";

/** "YYYY-MM-DDTHH:MM" of an instant in the organization's time zone (UTC without one), for a datetime-local field. */
function localInput(iso: string | null, timeZone: string | null): string {
  if (iso === null) return "";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: timeZone ?? "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(new Date(iso))
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

/**
 * Edits ONE line with the same rules as when it was added (FastAPI enforces them too):
 *  - a catalog line: another catalog item, the quantity and the discount (description, unit, price and VAT come from
 *    the item);
 *  - a service: another service, for whom, by whom, when, the quantity, notes and the discount;
 *  - an ad-hoc line: description, unit, quantity, price, VAT and the discount.
 * A line never changes kind. The edit is based on the line as it was when the editor opened (`base`, including its
 * version): if the server's line has moved on since, or FastAPI refuses the version, the user's draft is KEPT on screen,
 * saving is switched off, and the user chooses to discard it and load the latest. Only what changed is sent.
 */
export function LineEditor({ line, ordinal, onClose }: { line: TransactionLine; ordinal: number; onClose: () => void }) {
  useRegisterEditor();
  const orgId = useOrgId();
  const { transaction, timeZone, busy, mutate, report, refresh } = useEditor();
  const kind: Kind = line.kind === "service" ? "service" : line.item_id !== null ? "item" : "adhoc";

  const [base] = useState(() => ({
    description: line.description,
    unit: line.unit,
    quantity: line.quantity as string,
    // The price the line's own discount applies to, so saving the price never applies the discount twice.
    unit_price_ex_vat: (line.price_before_line_discount ?? line.unit_price_ex_vat) as string,
    vat_rate: line.vat_rate as string,
    line_discount_percent: (line.line_discount_percent ?? "") as string,
    notes: line.notes ?? "",
    performed_at: localInput(line.performed_at, timeZone),
    performed_by_user_id: line.performed_by ?? "",
    version: line.version,
  }));
  const startItem: PickerEntity | null = line.item_id ? { id: line.item_id, label: line.description } : null;
  const startSubject: PickerEntity | null = line.subject_id ? { id: line.subject_id, label: line.subject_label ?? "a record that no longer exists" } : null;
  const [item, setItem] = useState<PickerEntity | null>(startItem);
  const [subjectType, setSubjectType] = useState(line.subject_type ?? SUBJECT_KINDS[0].type);
  const [subject, setSubject] = useState<PickerEntity | null>(startSubject);
  const subjectKind = SUBJECT_KINDS.find((entry) => entry.type === subjectType) ?? SUBJECT_KINDS[0];
  const subjectSearch = useMemo(() => subjectKind.search(orgId), [subjectKind, orgId]);
  const search = useMemo(() => itemSearch(orgId, kind === "service" ? { type: "service" } : {}), [orgId, kind]);
  const [colleagues, setColleagues] = useState<Colleague[]>([]);
  useEffect(() => {
    if (kind !== "service") return;
    const controller = new AbortController();
    void apiFetch<Colleague[]>(orgId, "/members/people", { signal: controller.signal }).then((result) => {
      if (result.ok && Array.isArray(result.data)) setColleagues(result.data);
    });
    return () => controller.abort();
  }, [kind, orgId]);
  const [draft, setDraft] = useState({
    description: base.description,
    unit: base.unit,
    quantity: base.quantity,
    unit_price_ex_vat: base.unit_price_ex_vat,
    vat_rate: base.vat_rate,
    line_discount_percent: base.line_discount_percent,
    notes: base.notes,
    performed_at: base.performed_at,
    performed_by_user_id: base.performed_by_user_id,
  });
  const [local, setLocal] = useState<FieldErrors>({});
  const [problems, setProblems] = useState<Problems>(NO_PROBLEMS);
  const [refused, setRefused] = useState(false);
  const [saving, setSaving] = useState(false);

  const changedElsewhere = refused || line.version !== base.version;
  const errorsFor = (name: string) => local[name] ?? problems.byField[name];
  const set = (key: keyof typeof draft) => (value: string) => setDraft((current) => ({ ...current, [key]: value }));

  async function save() {
    setLocal({});
    setProblems(NO_PROBLEMS);

    const body: LineUpdate = {};
    const shapeErrors: FieldErrors = {};
    if (kind === "adhoc") {
      if (draft.description !== base.description) body.description = draft.description;
      if (draft.unit !== base.unit) body.unit = draft.unit;
    } else {
      if (item === null) shapeErrors.item_id = [kind === "service" ? "Choose a service." : "Choose an item."];
      else if (item.id !== line.item_id) body.item_id = item.id;
    }
    if (kind === "service") {
      if (subject === null) shapeErrors.subject_id = ["Choose who or what the service was for."];
      else if (subject.id !== line.subject_id || subjectType !== line.subject_type) {
        body.subject_type = subjectType;
        body.subject_id = subject.id;
      }
      if (draft.performed_at !== base.performed_at && draft.performed_at !== "") body.performed_at = draft.performed_at;
      if (draft.performed_by_user_id !== base.performed_by_user_id) body.performed_by_user_id = draft.performed_by_user_id === "" ? null : draft.performed_by_user_id;
      if (draft.notes !== base.notes) body.notes = draft.notes.trim() === "" ? null : draft.notes;
    }
    // Decimals: compared as strings, checked for shape only, sent as strings.
    const quantity = draft.quantity.trim();
    if (quantity !== base.quantity) {
      const parsed = parseQuantity(quantity);
      if (parsed === null) shapeErrors.quantity = [NOT_A_DECIMAL];
      else body.quantity = parsed;
    }
    const price = draft.unit_price_ex_vat.trim();
    if (kind === "adhoc" && price !== base.unit_price_ex_vat) {
      const parsed = parseMoney(price);
      if (parsed === null) shapeErrors.unit_price_ex_vat = [NOT_A_DECIMAL];
      else body.unit_price_ex_vat = parsed;
    }
    const vat = draft.vat_rate.trim();
    if (kind === "adhoc" && vat !== base.vat_rate) {
      const parsed = parsePercent(vat);
      if (parsed === null) shapeErrors.vat_rate = [NOT_A_DECIMAL];
      else body.vat_rate = parsed;
    }
    const discount = draft.line_discount_percent.trim();
    if (discount !== base.line_discount_percent) {
      const parsed = discount === "" ? null : parsePercent(discount);
      if (discount !== "" && parsed === null) shapeErrors.line_discount_percent = [NOT_A_DECIMAL];
      else body.line_discount_percent = parsed;
    }
    if (Object.keys(shapeErrors).length > 0) {
      setLocal(shapeErrors);
      return;
    }
    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }

    setSaving(true);
    const result = await mutate(() =>
      apiFetch<TransactionLine>(orgId, `/transactions/${transaction.id}/lines/${line.id}`, { method: "PATCH", body, ifMatch: base.version }),
    );
    setSaving(false);
    if (result === null) return;
    if (result.ok) return onClose();

    const failure = classify(result.error);
    if (failure.kind === "validation") setProblems(problemsFrom(result.error, CONTROLS));
    else if (failure.kind === "stale") {
      // Keep the draft (this editor holds its own state; a refresh only updates what the page
      // shows around it) and load what is on the server now, so the notice can show it.
      setRefused(true);
      refresh();
    }
    else report(failure, "save");
  }

  return (
    <form
      aria-label={`Edit line ${ordinal}`}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        if (!changedElsewhere) void save();
      }}
      data-testid="line-editor"
      className="flex flex-col gap-3 rounded border border-zinc-300 p-3 dark:border-zinc-700"
    >
      <h3 className="text-sm font-medium">Edit line {ordinal}</h3>
      {changedElsewhere && (
        <Notice tone="error" testId="line-conflict">
          <p>{TEXT.lineChanged}</p>
          {line.version !== base.version && (
            <p className="mt-1">
              Now on the server: {line.description} · quantity <DecimalText value={line.quantity} />, unit {line.unit} × <DecimalText value={line.unit_price_ex_vat} /> at{" "}
              <DecimalText value={line.vat_rate} /> % VAT.
            </p>
          )}
          <Button
            type="button"
            className="mt-2"
            onClick={() => {
              refresh();
              onClose();
            }}
            data-testid="discard-line"
          >
            Discard my edits and load the latest
          </Button>
        </Notice>
      )}
      {kind === "adhoc" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Description" name="description" value={draft.description} onChange={set("description")} error={errorsFor("description")} autoComplete="off" />
          <TextField label="Unit" name="unit" value={draft.unit} onChange={set("unit")} error={errorsFor("unit")} autoComplete="off" />
          <DecimalField label="Quantity" name="quantity" value={draft.quantity} onChange={set("quantity")} error={errorsFor("quantity")} />
          <DecimalField
            label={line.line_discount_percent ? "Unit price excluding VAT (before the line discount)" : "Unit price excluding VAT"}
            name="unit_price_ex_vat"
            value={draft.unit_price_ex_vat}
            onChange={set("unit_price_ex_vat")}
            error={errorsFor("unit_price_ex_vat")}
          />
          <DecimalField label="VAT rate (%)" name="vat_rate" value={draft.vat_rate} onChange={set("vat_rate")} error={errorsFor("vat_rate")} />
          <DecimalField label="Discount % (optional)" name="line_discount_percent" value={draft.line_discount_percent} onChange={set("line_discount_percent")} error={errorsFor("line_discount_percent")} hint="Empty removes the discount." />
        </div>
      ) : (
        <div className="flex flex-col gap-3" data-testid={kind === "service" ? "edit-service-fields" : "edit-item-fields"}>
          <EntityPicker
            label={kind === "service" ? "Service" : "Item"}
            name="item_id"
            value={item}
            onChange={setItem}
            search={search}
            error={errorsFor("item_id")}
            hint="Description, unit, price and VAT come from the catalog; change the price with the discount."
          />
          {kind === "service" && (
            <>
              <label className="flex flex-col gap-1 text-sm font-medium">
                Performed for
                <select
                  name="subject_type"
                  value={subjectType}
                  onChange={(event) => {
                    setSubjectType(event.target.value);
                    setSubject(null);
                  }}
                  className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900"
                >
                  {SUBJECT_KINDS.map((entry) => (
                    <option key={entry.type} value={entry.type}>
                      {entry.label}
                    </option>
                  ))}
                </select>
              </label>
              <EntityPicker key={subjectType} label={subjectKind.label} name="subject_id" value={subject} onChange={setSubject} search={subjectSearch} error={errorsFor("subject_id")} />
              <label className="flex flex-col gap-1 text-sm font-medium">
                Performed by
                <select
                  name="performed_by_user_id"
                  value={draft.performed_by_user_id}
                  onChange={(event) => set("performed_by_user_id")(event.target.value)}
                  className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900"
                >
                  <option value="">Not recorded</option>
                  {line.performed_by && !colleagues.some((colleague) => colleague.user_id === line.performed_by) && (
                    <option value={line.performed_by}>{line.performed_by_name ?? "Current performer"}</option>
                  )}
                  {colleagues.map((colleague) => (
                    <option key={colleague.user_id} value={colleague.user_id}>
                      {colleague.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-sm font-medium">
                Performed at
                <input
                  type="datetime-local"
                  name="performed_at"
                  value={draft.performed_at}
                  onChange={(event) => set("performed_at")(event.target.value)}
                  className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900"
                />
              </label>
            </>
          )}
          <DecimalField label="Quantity" name="quantity" value={draft.quantity} onChange={set("quantity")} error={errorsFor("quantity")} />
          {kind === "service" && <TextField label="Notes" name="notes" value={draft.notes} onChange={set("notes")} error={errorsFor("notes")} autoComplete="off" />}
          <DecimalField label="Discount % (optional)" name="line_discount_percent" value={draft.line_discount_percent} onChange={set("line_discount_percent")} error={errorsFor("line_discount_percent")} hint="Empty removes the discount." />
        </div>
      )}
      <ErrorSummary messages={problems.general} />
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={busy || changedElsewhere} data-testid="save-line">
          {saving ? "Saving…" : "Save line"}
        </Button>
        <Button type="button" disabled={saving} onClick={onClose} data-testid="cancel-line">
          Cancel
        </Button>
      </div>
    </form>
  );
}
