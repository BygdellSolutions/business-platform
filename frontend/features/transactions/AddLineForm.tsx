"use client";

import { useMemo, useState } from "react";

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
import type { LineCreate, TransactionLine } from "@/lib/api/types";
import { parseMoney, parsePercent, parseQuantity } from "@/lib/decimal";
import { NOT_A_DECIMAL, NO_PROBLEMS, problemsFrom, type Problems } from "@/lib/forms";

const CONTROLS = ["item_id", "description", "unit", "quantity", "unit_price_ex_vat", "vat_rate"] as const;

type Mode = "item" | "adhoc";

/**
 * Add a line, in one of two ways.
 *
 *  - From the catalog: the user picks an item and a quantity, and ONLY `{item_id, quantity}` is
 *    sent. FastAPI copies the item's name, unit, price and VAT (the snapshot) and the line then
 *    appears in the table as stored, where it can be edited. The frontend never reads those
 *    values from the item, so it cannot copy a price that has since changed.
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
  const errorsFor = (name: string) => local[name] ?? problems.byField[name];
  const set = (key: keyof typeof fields) => (value: string) => setFields((current) => ({ ...current, [key]: value }));

  function build(): { body: LineCreate } | { errors: FieldErrors } {
    const errors: FieldErrors = {};
    const quantity = parseQuantity(fields.quantity.trim());
    if (quantity === null) errors.quantity = [NOT_A_DECIMAL];

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
          <input type="radio" name="line-kind" checked={mode === "adhoc"} onChange={() => setMode("adhoc")} /> Ad-hoc line
        </label>
      </div>

      {mode === "item" ? (
        <>
          <EntityPicker label="Item" name="item_id" value={item} onChange={setItem} search={search} error={errorsFor("item_id")} hint="Its name, unit, price and VAT are copied by the server when the line is added." />
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
