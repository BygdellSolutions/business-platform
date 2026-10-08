"use client";

import { useEffect, useMemo, useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { EntityPicker, type PickerEntity } from "@/components/ui/EntityPicker";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { DecimalField, TextField } from "@/components/ui/Field";
import { itemSearch } from "@/features/catalog/item-picker";
import { useEditor, useRegisterEditor } from "@/features/transactions/editor-context";
import { classify } from "@/features/transactions/failures";
import { apiFetch } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/errors";
import { SUBJECT_KINDS } from "@/features/transactions/service-subjects";
import type { Colleague, ItemAvailability, LineCreate, TransactionLine } from "@/lib/api/types";
import { parseMoney, parsePercent, parseQuantity, trimQuantity } from "@/lib/decimal";
import { NOT_A_DECIMAL, NO_PROBLEMS, problemsFrom, type Problems } from "@/lib/forms";

const CONTROLS = ["item_id", "description", "unit", "quantity", "unit_price_ex_vat", "vat_rate", "subject_id", "performed_by_user_id", "performed_at", "notes"] as const;

type Mode = "item" | "service" | "adhoc";

/**
 * Add a line, in one of three ways (in this order).
 *
 *  - From the catalog: the user picks an item and a quantity, and ONLY `{item_id, quantity}` is
 *    sent. FastAPI copies the item's name, unit, price and VAT (the snapshot) and the line then
 *    appears in the table as stored, where it can be edited. The frontend never reads those
 *    values from the item, so it cannot copy a price that has since changed.
 *  - Service: work performed for someone or something (a horse, a person): a service from the catalog, for whom,
 *    by whom (a member), when (empty: now) and notes. Priced like a catalog item, discounts included.
 *  - Ad-hoc: no item; description, unit, quantity, price and VAT are typed (as strings).
 */
export function AddLineForm() {
  const { busy } = useEditor();
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <div>
        <Button type="button" disabled={busy} onClick={() => setOpen(true)} data-testid="add-line">
          Add line
        </Button>
      </div>
    );
  }
  return <AddLinePanel onClose={() => setOpen(false)} />;
}

function AddLinePanel({ onClose }: { onClose: () => void }) {
  useRegisterEditor();
  const orgId = useOrgId();
  const { transaction, busy, mutate, report } = useEditor();
  const [mode, setMode] = useState<Mode>("item");
  const [item, setItem] = useState<PickerEntity | null>(null);
  const [fields, setFields] = useState({ quantity: "", description: "", unit: "", unit_price_ex_vat: "", vat_rate: "" });
  const [local, setLocal] = useState<FieldErrors>({});
  const [problems, setProblems] = useState<Problems>(NO_PROBLEMS);
  const [saving, setSaving] = useState(false);

  const search = useMemo(() => itemSearch(orgId), [orgId]);
  const serviceSearch = useMemo(() => itemSearch(orgId, { type: "service" }), [orgId]);
  const [subjectType, setSubjectType] = useState(SUBJECT_KINDS[0].type);
  const [subject, setSubject] = useState<PickerEntity | null>(null);
  const subjectKind = SUBJECT_KINDS.find((kind) => kind.type === subjectType) ?? SUBJECT_KINDS[0];
  const subjectSearch = useMemo(() => subjectKind.search(orgId), [subjectKind, orgId]);
  const [service, setService] = useState({ performed_by_user_id: "", performed_at: "", notes: "" });
  const [colleagues, setColleagues] = useState<Colleague[]>([]);
  const [availability, setAvailability] = useState<ItemAvailability | null>(null);
  useEffect(() => {
    if (mode !== "item" || item === null) return;
    const controller = new AbortController();
    void apiFetch<ItemAvailability[]>(orgId, `/inventory/availability?${new URLSearchParams({ item_id: item.id })}`, { signal: controller.signal }).then((result) => {
      // Only a product that tracks stock is answered; anything else shows nothing.
      if (result.ok && Array.isArray(result.data)) setAvailability(result.data[0] ?? null);
    });
    return () => controller.abort();
  }, [mode, item, orgId]);
  useEffect(() => {
    if (mode !== "service" || colleagues.length > 0) return;
    const controller = new AbortController();
    void apiFetch<Colleague[]>(orgId, "/members/people", { signal: controller.signal }).then((result) => {
      if (result.ok) setColleagues(result.data);
    });
    return () => controller.abort();
  }, [mode, orgId, colleagues.length]);
  const errorsFor = (name: string) => local[name] ?? problems.byField[name];
  const set = (key: keyof typeof fields) => (value: string) => setFields((current) => ({ ...current, [key]: value }));

  function build(): { body: LineCreate } | { errors: FieldErrors } {
    const errors: FieldErrors = {};
    const quantity = parseQuantity(fields.quantity.trim());
    if (quantity === null) errors.quantity = [NOT_A_DECIMAL];

    if (mode === "service") {
      if (item === null) errors.item_id = ["Choose a service."];
      if (subject === null) errors.subject_id = ["Choose who or what the service was for."];
      if (Object.keys(errors).length > 0 || quantity === null || item === null || subject === null) return { errors };
      return {
        body: {
          kind: "service",
          item_id: item.id,
          quantity,
          subject_type: subjectType,
          subject_id: subject.id,
          ...(service.performed_at !== "" ? { performed_at: service.performed_at } : {}),
          performed_by_user_id: service.performed_by_user_id === "" ? null : service.performed_by_user_id,
          notes: service.notes.trim() === "" ? null : service.notes,
        },
      };
    }

    if (mode === "item") {
      if (item === null) errors.item_id = ["Choose an item."];
      if (Object.keys(errors).length > 0 || quantity === null || item === null) return { errors };
      return { body: { item_id: item.id, quantity } }; // nothing else: FastAPI takes the rest from the item
    }

    const price = parseMoney(fields.unit_price_ex_vat.trim());
    const vat = parsePercent(fields.vat_rate.trim());
    if (price === null) errors.unit_price_ex_vat = [NOT_A_DECIMAL];
    if (vat === null) errors.vat_rate = [NOT_A_DECIMAL];
    if (Object.keys(errors).length > 0 || quantity === null || price === null || vat === null) return { errors };
    return { body: { description: fields.description, unit: fields.unit, quantity, unit_price_ex_vat: price, vat_rate: vat } };
  }

  async function submit() {
    setLocal({});
    setProblems(NO_PROBLEMS);
    const built = build();
    if ("errors" in built) {
      setLocal(built.errors);
      return;
    }
    setSaving(true);
    const result = await mutate(() => apiFetch<TransactionLine>(orgId, `/transactions/${transaction.id}/lines`, { method: "POST", body: built.body }));
    setSaving(false);
    if (result === null) return;
    if (result.ok) return onClose();

    const failure = classify(result.error);
    if (failure.kind === "validation") setProblems(problemsFrom(result.error, CONTROLS));
    else report(failure, "save");
  }

  return (
    <form
      aria-label="Add line"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      data-testid="add-line-form"
      className="flex max-w-2xl flex-col gap-3 rounded border border-zinc-300 p-3 dark:border-zinc-700"
    >
      <h3 className="text-sm font-medium">Add line</h3>
      <div role="group" aria-label="Kind of line" className="flex gap-4 text-sm">
        <label className="flex items-center gap-1">
          <input type="radio" name="line-kind" checked={mode === "item"} onChange={() => setMode("item")} /> Catalog item
        </label>
        <label className="flex items-center gap-1">
          <input
            type="radio"
            name="line-kind"
            checked={mode === "service"}
            onChange={() => {
              setMode("service");
              setItem(null); // a product chosen as a catalog item is not a service
            }}
          />{" "}
          Service
        </label>
        <label className="flex items-center gap-1">
          <input type="radio" name="line-kind" checked={mode === "adhoc"} onChange={() => setMode("adhoc")} /> Ad-hoc line
        </label>
      </div>

      {mode === "service" ? (
        <div className="flex flex-col gap-3" data-testid="service-fields">
          <EntityPicker label="Service" name="item_id" value={item} onChange={setItem} search={serviceSearch} error={errorsFor("item_id")} hint="From the catalog's services; priced like any catalog line." />
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
              {SUBJECT_KINDS.map((kind) => (
                <option key={kind.type} value={kind.type}>
                  {kind.label}
                </option>
              ))}
            </select>
          </label>
          <EntityPicker key={subjectType} label={subjectKind.label} name="subject_id" value={subject} onChange={setSubject} search={subjectSearch} error={errorsFor("subject_id")} />
          <label className="flex flex-col gap-1 text-sm font-medium">
            Performed by
            <select
              name="performed_by_user_id"
              value={service.performed_by_user_id}
              onChange={(event) => setService((current) => ({ ...current, performed_by_user_id: event.target.value }))}
              className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900"
            >
              <option value="">Not recorded</option>
              {colleagues.map((colleague) => (
                <option key={colleague.user_id} value={colleague.user_id}>
                  {colleague.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium">
            Performed at (leave empty for now)
            <input
              type="datetime-local"
              name="performed_at"
              value={service.performed_at}
              onChange={(event) => setService((current) => ({ ...current, performed_at: event.target.value }))}
              className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900"
            />
          </label>
          <DecimalField label="Quantity" name="quantity" value={fields.quantity} onChange={set("quantity")} error={errorsFor("quantity")} />
          <TextField label="Notes" name="notes" value={service.notes} onChange={(value) => setService((current) => ({ ...current, notes: value }))} error={errorsFor("notes")} autoComplete="off" />
        </div>
      ) : mode === "item" ? (
        <>
          <EntityPicker label="Item" name="item_id" value={item} onChange={setItem} search={search} error={errorsFor("item_id")} hint="Its name, unit, price and VAT are copied by the server when the line is added." />
          {availability && availability.item_id === item?.id && (
            <p className="text-sm text-zinc-600 dark:text-zinc-400" data-testid="item-availability">
              In stock: {trimQuantity(availability.on_hand)}, on other drafts: {trimQuantity(availability.allocated)}, available: {trimQuantity(availability.available)}. A
              shortage is backordered at completion; the line is never refused.
            </p>
          )}
          <DecimalField label="Quantity" name="quantity" value={fields.quantity} onChange={set("quantity")} error={errorsFor("quantity")} />
        </>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Description" name="description" value={fields.description} onChange={set("description")} error={errorsFor("description")} autoComplete="off" />
          <TextField label="Unit" name="unit" value={fields.unit} onChange={set("unit")} error={errorsFor("unit")} autoComplete="off" />
          <DecimalField label="Quantity" name="quantity" value={fields.quantity} onChange={set("quantity")} error={errorsFor("quantity")} />
          <DecimalField label="Unit price excluding VAT" name="unit_price_ex_vat" value={fields.unit_price_ex_vat} onChange={set("unit_price_ex_vat")} error={errorsFor("unit_price_ex_vat")} />
          <DecimalField label="VAT rate (%)" name="vat_rate" value={fields.vat_rate} onChange={set("vat_rate")} error={errorsFor("vat_rate")} />
        </div>
      )}

      <ErrorSummary messages={problems.general} />
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={busy} data-testid="submit-line">
          {saving ? "Adding…" : "Add"}
        </Button>
        <Button type="button" disabled={saving} onClick={onClose} data-testid="cancel-add-line">
          Cancel
        </Button>
      </div>
    </form>
  );
}
