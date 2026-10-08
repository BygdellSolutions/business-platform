"use client";

import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { DecimalText } from "@/components/ui/DecimalText";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { DecimalField, TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { useEditor, useRegisterEditor } from "@/features/transactions/editor-context";
import { classify, TEXT } from "@/features/transactions/failures";
import { apiFetch } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/errors";
import type { LineUpdate, TransactionLine } from "@/lib/api/types";
import { parseMoney, parsePercent, parseQuantity } from "@/lib/decimal";
import { NOT_A_DECIMAL, NO_PROBLEMS, problemsFrom, type Problems } from "@/lib/forms";

const CONTROLS = ["description", "unit", "quantity", "unit_price_ex_vat", "vat_rate", "line_discount_percent"] as const;

/**
 * Edits ONE line's own snapshot values. The edit is based on the line as it was when the
 * editor opened (`base`, including its version): if the server's line has moved on since, or
 * FastAPI refuses the version, the user's draft is KEPT on screen, saving is switched off, and
 * the user chooses to discard it and load the latest. Only the fields that changed are sent, as
 * strings, and never an `item_id`: editing a line cannot touch or re-copy the catalog item.
 */
export function LineEditor({ line, ordinal, onClose }: { line: TransactionLine; ordinal: number; onClose: () => void }) {
  useRegisterEditor();
  const orgId = useOrgId();
  const { transaction, busy, mutate, report, refresh } = useEditor();

  const [base] = useState(() => ({
    description: line.description,
    unit: line.unit,
    quantity: line.quantity as string,
    // The price the line's own discount applies to, so saving the price never applies the discount twice.
    unit_price_ex_vat: (line.price_before_line_discount ?? line.unit_price_ex_vat) as string,
    vat_rate: line.vat_rate as string,
    line_discount_percent: (line.line_discount_percent ?? "") as string,
    version: line.version,
  }));
  const [draft, setDraft] = useState({
    description: base.description,
    unit: base.unit,
    quantity: base.quantity,
    unit_price_ex_vat: base.unit_price_ex_vat,
    vat_rate: base.vat_rate,
    line_discount_percent: base.line_discount_percent,
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
    if (draft.description !== base.description) body.description = draft.description;
    if (draft.unit !== base.unit) body.unit = draft.unit;
    // Decimals: compared as strings, checked for shape only, sent as strings.
    const quantity = draft.quantity.trim();
    if (quantity !== base.quantity) {
      const parsed = parseQuantity(quantity);
      if (parsed === null) shapeErrors.quantity = [NOT_A_DECIMAL];
      else body.quantity = parsed;
    }
    const price = draft.unit_price_ex_vat.trim();
    if (price !== base.unit_price_ex_vat) {
      const parsed = parseMoney(price);
      if (parsed === null) shapeErrors.unit_price_ex_vat = [NOT_A_DECIMAL];
      else body.unit_price_ex_vat = parsed;
    }
    const vat = draft.vat_rate.trim();
    if (vat !== base.vat_rate) {
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
              Now on the server: {line.description} · <DecimalText value={line.quantity} /> {line.unit} × <DecimalText value={line.unit_price_ex_vat} /> at <DecimalText value={line.vat_rate} /> % VAT.
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
