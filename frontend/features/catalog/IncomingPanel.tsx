"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { EntityPicker, type PickerEntity } from "@/components/ui/EntityPicker";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { DecimalText } from "@/components/ui/DecimalText";
import { DecimalField, TextField } from "@/components/ui/Field";
import { SupplierName } from "@/features/suppliers/SupplierName";
import { supplierSearch } from "@/features/suppliers/supplier-picker";
import { apiFetch } from "@/lib/api/client";
import type { Incoming } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";
import { trimQuantity } from "@/lib/decimal";
import { formatDay } from "@/lib/timestamps";

const CONTROLS = [
  "quantity",
  "expected_on",
  "supplier_id",
  "reference",
  "unit_cost",
] as const;
const DATE =
  "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900";

const isOpen = (row: Incoming) =>
  row.state === "expected" || row.state === "partially_received";

const STATES: Record<Incoming["state"], string> = {
  expected: "Expected",
  partially_received: "Partly received",
  received: "Received",
  cancelled: "Cancelled",
};

/**
 * Stock on its way for one product: what was ordered, from whom and when it is expected. Incoming is never on hand:
 * a person receives it (all of it, or a part), and only then does it count. A receipt does not hand anything to a
 * waiting backorder by itself; that is a separate, confirmed step. Received and cancelled deliveries stay listed, so
 * what was paid per unit can be followed over time.
 */
export function IncomingPanel({
  itemId,
  unit,
  incoming,
  canWrite,
  timeZone,
}: {
  itemId: string;
  unit: string;
  incoming: Incoming[];
  canWrite: boolean;
  timeZone: string | null;
}) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [form, setForm] = useState({
    quantity: "",
    expected_on: "",
    reference: "",
    unit_cost: "",
  });
  const [editing, setEditing] = useState<{
    id: string;
    expected_on: string;
    unit_cost: string;
  } | null>(null);
  const [supplier, setSupplier] = useState<PickerEntity | null>(null);
  const search = useMemo(
    () => supplierSearch(orgId, { activeOnly: true }),
    [orgId],
  );
  const [receiving, setReceiving] = useState<Record<string, string>>({});
  const problems = problemsFrom(error, CONTROLS);
  // A row's edit has its own errors, shown in that row (not under the "record a delivery" form below).
  const editMutation = useMutation();
  const editProblems = problemsFrom(editMutation.error, []);
  const set = (key: keyof typeof form) => (value: string) =>
    setForm((current) => ({ ...current, [key]: value }));

  async function create(event: FormEvent) {
    event.preventDefault();
    const body = {
      item_id: itemId,
      quantity: form.quantity.trim(),
      expected_on: blankToNull(form.expected_on),
      supplier_id: supplier?.id ?? null,
      reference: blankToNull(form.reference),
      unit_cost: blankToNull(form.unit_cost),
    };
    const created = await run(() =>
      apiFetch<Incoming>(orgId, "/inventory/incoming", {
        method: "POST",
        body,
      }),
    );
    if (created === null) return;
    setForm({ quantity: "", expected_on: "", reference: "", unit_cost: "" });
    setSupplier(null);
    router.refresh();
  }

  async function receive(row: Incoming) {
    const quantity = (receiving[row.id] ?? "").trim();
    const body = quantity === "" ? {} : { quantity };
    const received = await run(() =>
      apiFetch<Incoming>(orgId, `/inventory/incoming/${row.id}/receive`, {
        method: "POST",
        body,
      }),
    );
    if (received === null) return;
    setReceiving((current) => ({ ...current, [row.id]: "" }));
    router.refresh();
  }

  async function saveEdit(row: Incoming) {
    if (editing === null) return;
    // Both values are sent as typed; the backend validates them. The date only while something is still expected.
    const body: Record<string, string | null> = {
      unit_cost: blankToNull(editing.unit_cost),
    };
    if (isOpen(row)) body.expected_on = blankToNull(editing.expected_on);
    const saved = await editMutation.run(() =>
      apiFetch<Incoming>(orgId, `/inventory/incoming/${row.id}`, {
        method: "PATCH",
        body,
      }),
    );
    if (saved === null) return;
    setEditing(null);
    router.refresh();
  }

  async function cancel(row: Incoming) {
    const cancelled = await run(() =>
      apiFetch<Incoming>(orgId, `/inventory/incoming/${row.id}/cancel`, {
        method: "POST",
      }),
    );
    if (cancelled !== null) router.refresh();
  }

  return (
    <section
      aria-label="Incoming stock"
      data-testid="incoming-panel"
      className="flex max-w-3xl flex-col gap-3"
    >
      <h2 className="text-lg font-semibold">Incoming</h2>
      {incoming.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-incoming">
          No deliveries yet.
        </p>
      ) : (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Order date</th>
              <th className="py-1 pr-4">Expected</th>
              <th className="py-1 pr-4">Unit</th>
              <th className="py-1 pr-4 text-right">Ordered</th>
              <th className="py-1 pr-4 text-right">Received</th>
              <th className="py-1 pr-4 text-right">Still expected</th>
              <th className="py-1 pr-4">Supplier</th>
              <th className="py-1 pr-4">Reference</th>
              <th className="py-1 pr-4 text-right">Unit cost excl. VAT</th>
              <th className="py-1 pr-4">State</th>
              {canWrite && <th className="py-1">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {incoming.map((row) => (
              <tr
                key={row.id}
                data-testid="incoming-row"
                className="border-b border-zinc-200 align-top dark:border-zinc-800"
              >
                <td className="py-1 pr-4" data-testid="incoming-ordered-on">
                  {formatDay(row.created_at, timeZone)}
                </td>
                <td className="py-1 pr-4" data-testid="incoming-expected">
                  {row.expected_on ?? (
                    <span className="text-zinc-500">not given</span>
                  )}
                </td>
                <td className="py-1 pr-4">{unit}</td>
                <td
                  className="py-1 pr-4 text-right"
                  data-testid="incoming-ordered"
                >
                  {trimQuantity(row.quantity)}
                </td>
                <td className="py-1 pr-4 text-right">
                  {trimQuantity(row.received)}
                </td>
                <td
                  className="py-1 pr-4 text-right"
                  data-testid="incoming-remaining"
                >
                  {trimQuantity(row.remaining)}
                </td>
                <td className="py-1 pr-4">
                  <SupplierName orgId={orgId} supplier={row.supplier} />
                </td>
                <td className="py-1 pr-4">{row.reference}</td>
                <td
                  className="py-1 pr-4 text-right"
                  data-testid="incoming-unit-cost"
                >
                  {row.unit_cost !== null && (
                    <DecimalText value={row.unit_cost} />
                  )}
                </td>
                <td className="py-1 pr-4">{STATES[row.state]}</td>
                {canWrite && (
                  <td className="py-1">
                    {editing?.id === row.id ? (
                      <div
                        className="flex flex-wrap items-center gap-2"
                        data-testid="incoming-edit"
                      >
                        {isOpen(row) && (
                          <input
                            type="date"
                            aria-label="Expected on"
                            value={editing.expected_on}
                            onChange={(event) =>
                              setEditing({
                                ...editing,
                                expected_on: event.target.value,
                              })
                            }
                            className={DATE}
                          />
                        )}
                        <input
                          aria-label="Unit cost excl. VAT"
                          inputMode="decimal"
                          value={editing.unit_cost}
                          onChange={(event) =>
                            setEditing({
                              ...editing,
                              unit_cost: event.target.value,
                            })
                          }
                          className="w-28 rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900"
                        />
                        <Button
                          type="button"
                          disabled={editMutation.pending}
                          onClick={() => void saveEdit(row)}
                          data-testid="save-incoming"
                        >
                          Save
                        </Button>
                        <button
                          type="button"
                          className="text-sm underline"
                          onClick={() => setEditing(null)}
                        >
                          Cancel editing
                        </button>
                        <ErrorSummary messages={editProblems.general} />
                      </div>
                    ) : (
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          className="text-sm underline"
                          data-testid="edit-incoming"
                          onClick={() =>
                            setEditing({
                              id: row.id,
                              expected_on: row.expected_on ?? "",
                              unit_cost: row.unit_cost ?? "",
                            })
                          }
                        >
                          Edit
                        </button>
                        {isOpen(row) && (
                          <>
                            <input
                              aria-label="Quantity received"
                              inputMode="decimal"
                              placeholder={trimQuantity(row.remaining)}
                              value={receiving[row.id] ?? ""}
                              onChange={(event) =>
                                setReceiving((current) => ({
                                  ...current,
                                  [row.id]: event.target.value,
                                }))
                              }
                              className="w-24 rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900"
                            />
                            <Button
                              type="button"
                              disabled={pending}
                              onClick={() => void receive(row)}
                              data-testid="receive-incoming"
                            >
                              Receive
                            </Button>
                            <ConfirmButton
                              label="Cancel"
                              question="Will the rest of this delivery not come? Units already received stay on hand."
                              confirmLabel="Yes, cancel the rest"
                              disabled={pending}
                              onConfirm={() => void cancel(row)}
                              testId="cancel-incoming"
                            />
                          </>
                        )}
                      </div>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canWrite && (
        <form
          onSubmit={create}
          noValidate
          aria-label="Record incoming stock"
          className="flex flex-col gap-3"
        >
          <h3 className="font-medium">Record a delivery on its way</h3>
          <DecimalField
            label={`Quantity (unit: ${unit})`}
            name="quantity"
            value={form.quantity}
            onChange={set("quantity")}
            error={problems.byField.quantity}
          />
          <label className="flex flex-col gap-1 text-sm font-medium">
            Expected on (optional)
            <input
              type="date"
              name="expected_on"
              value={form.expected_on}
              onChange={(event) => set("expected_on")(event.target.value)}
              className={DATE}
            />
          </label>
          <EntityPicker
            label="Supplier (optional)"
            name="supplier_id"
            value={supplier}
            onChange={setSupplier}
            search={search}
            clearable
            error={problems.byField.supplier_id}
          />
          <p className="-mt-2 text-xs text-zinc-500">
            Chosen from{" "}
            <Link href={`/o/${orgId}/suppliers`} className="underline">
              Suppliers
            </Link>
            ; add a new one there first.
          </p>
          <DecimalField
            label="Unit cost excl. VAT (optional)"
            name="unit_cost"
            value={form.unit_cost}
            onChange={set("unit_cost")}
            error={problems.byField.unit_cost}
            hint={`What you pay per unit (${unit}). It can be filled in later, for example from the supplier's invoice.`}
          />
          <TextField
            label="Reference (optional)"
            name="reference"
            value={form.reference}
            onChange={set("reference")}
            error={problems.byField.reference}
            autoComplete="off"
            hint="For example the purchase order number."
          />
          <ErrorSummary messages={problems.general} />
          <div>
            <Button
              type="submit"
              disabled={pending}
              data-testid="submit-incoming"
            >
              {pending ? "Saving…" : "Record incoming"}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
