"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { ActiveToggle } from "@/components/ui/ActiveToggle";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { CheckboxField, DecimalField, SelectField, TextAreaField, TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/errors";
import type { Item, ItemCreate, ItemType, ItemUpdate } from "@/lib/api/types";
import { parseMoney, parsePercent } from "@/lib/decimal";
import { NOT_A_DECIMAL, blankToNull, problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["type", "name", "description", "unit", "price_ex_vat", "vat_rate", "active", "sku", "track_stock"] as const;

const TYPES = [
  { value: "service", label: "Service" },
  { value: "product", label: "Product" },
];

/**
 * Every field is a string while the user edits, price and VAT included: a decimal typed as
 * "8.20" stays "8.20" in state, in the request body and on screen. Nothing here converts it
 * to a JavaScript number (ESLint enforces that for this folder). What the backend accepts
 * (digits, range, precision) is decided by the backend; only the shape is checked here.
 */
interface FormState {
  type: ItemType;
  name: string;
  description: string;
  unit: string;
  price_ex_vat: string;
  vat_rate: string;
  active: boolean;
  sku: string;
  track_stock: boolean;
}

function toState(item?: Item): FormState {
  return {
    type: item?.type ?? "service",
    name: item?.name ?? "",
    description: item?.description ?? "",
    unit: item?.unit ?? "",
    price_ex_vat: item?.price_ex_vat ?? "",
    vat_rate: item?.vat_rate ?? "",
    active: item?.active ?? true,
    sku: item?.sku ?? "",
    track_stock: item?.track_stock ?? false,
  };
}

export function ItemForm({ item }: { item?: Item }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [record, setRecord] = useState<Item | undefined>(item);
  const [state, setState] = useState<FormState>(() => toState(item));
  const [local, setLocal] = useState<FieldErrors>({});
  const [notice, setNotice] = useState<"saved" | "unchanged" | null>(null);

  const problems = problemsFrom(error, CONTROLS);
  const errorsFor = (name: string) => local[name] ?? problems.byField[name];
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((current) => ({ ...current, [key]: value }));

  /** The two decimal fields as typed, or the shape errors that stop the request. */
  function decimals() {
    const price = parseMoney(state.price_ex_vat.trim());
    const vat = parsePercent(state.vat_rate.trim());
    if (price !== null && vat !== null) return { ok: true as const, price, vat };
    const errors: FieldErrors = {};
    if (price === null) errors.price_ex_vat = [NOT_A_DECIMAL];
    if (vat === null) errors.vat_rate = [NOT_A_DECIMAL];
    return { ok: false as const, errors };
  }

  async function create() {
    const parsed = decimals();
    if (!parsed.ok) {
      setLocal(parsed.errors);
      return;
    }
    const body: ItemCreate = {
      type: state.type,
      name: state.name,
      description: blankToNull(state.description),
      unit: state.unit,
      price_ex_vat: parsed.price,
      vat_rate: parsed.vat,
      active: state.active,
      sku: blankToNull(state.sku),
      track_stock: state.type === "product" && state.track_stock,
    };
    const created = await run(() => apiFetch<Item>(orgId, "/items", { method: "POST", body }));
    if (created === null) return;
    router.push(`/o/${orgId}/catalog/${created.id}?created=1`);
    router.refresh(); // drop cached pages (the list visited before) so Back does not show them without the new record
  }

  async function save(current: Item) {
    const parsed = decimals();
    if (!parsed.ok) {
      setLocal(parsed.errors);
      return;
    }
    // Only what changed is sent. Decimals are compared as strings, never as numbers.
    const body: ItemUpdate = {};
    if (state.type !== current.type) body.type = state.type;
    if (state.name !== current.name) body.name = state.name;
    if (blankToNull(state.description) !== current.description) body.description = blankToNull(state.description);
    if (state.unit !== current.unit) body.unit = state.unit;
    if (parsed.price !== current.price_ex_vat) body.price_ex_vat = parsed.price;
    if (parsed.vat !== current.vat_rate) body.vat_rate = parsed.vat;
    if (blankToNull(state.sku) !== current.sku) body.sku = blankToNull(state.sku);
    const trackStock = state.type === "product" && state.track_stock;
    if (trackStock !== current.track_stock) body.track_stock = trackStock;
    if (Object.keys(body).length === 0) {
      setNotice("unchanged");
      return;
    }
    const saved = await run(() => apiFetch<Item>(orgId, `/items/${current.id}`, { method: "PATCH", body }));
    if (saved === null) return;
    setRecord(saved);
    setState(toState(saved)); // what the backend stored, exactly as it formatted it
    setNotice("saved");
    router.refresh();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    setLocal({});
    void (record ? save(record) : create());
  }

  return (
    <div className="flex max-w-xl flex-col gap-4">
      {record && (
        <ActiveToggle<Item>
          path={`/items/${record.id}`}
          active={record.active}
          noun="item"
          onChanged={(updated) => setRecord((current) => (current ? { ...current, active: updated.active, updated_at: updated.updated_at } : updated))}
        />
      )}
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4" aria-label={record ? "Edit item" : "New item"}>
        <SelectField label="Type" name="type" value={state.type} onChange={(value) => set("type", value as ItemType)} options={TYPES} error={errorsFor("type")} />
        <TextField label="Article number (SKU)" name="sku" value={state.sku} onChange={(value) => set("sku", value)} error={errorsFor("sku")} hint="Optional. Unique within the organization." autoComplete="off" />
        <TextField label="Name" name="name" value={state.name} onChange={(value) => set("name", value)} error={errorsFor("name")} autoComplete="off" />
        <TextAreaField label="Description" name="description" value={state.description} onChange={(value) => set("description", value)} error={errorsFor("description")} />
        <TextField label="Unit" name="unit" value={state.unit} onChange={(value) => set("unit", value)} error={errorsFor("unit")} hint="For example hour, piece or kg." autoComplete="off" />
        <DecimalField label="Price excluding VAT" name="price_ex_vat" value={state.price_ex_vat} onChange={(value) => set("price_ex_vat", value)} error={errorsFor("price_ex_vat")} />
        <DecimalField label="VAT rate (%)" name="vat_rate" value={state.vat_rate} onChange={(value) => set("vat_rate", value)} error={errorsFor("vat_rate")} />
        {state.type === "product" && (
          <CheckboxField label="Track stock" name="track_stock" checked={state.track_stock} onChange={(checked) => set("track_stock", checked)} error={errorsFor("track_stock")} />
        )}
        {!record && <CheckboxField label="Active" name="active" checked={state.active} onChange={(checked) => set("active", checked)} error={errorsFor("active")} />}
        <ErrorSummary messages={problems.general} />
        {notice === "saved" && <Notice testId="saved">Saved.</Notice>}
        {notice === "unchanged" && <Notice testId="unchanged">No changes to save.</Notice>}
        <div className="flex items-center gap-4">
          <Button type="submit" disabled={pending} data-testid="submit">
            {pending ? "Saving…" : record ? "Save changes" : "Create item"}
          </Button>
          <Link href={`/o/${orgId}/catalog`} className="text-sm underline">
            {record ? "Back to catalog" : "Cancel"}
          </Link>
        </div>
      </form>
    </div>
  );
}
