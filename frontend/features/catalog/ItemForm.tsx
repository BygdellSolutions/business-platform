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
import { parseMoney, parsePercent, type QuantityString } from "@/lib/decimal";
import { NOT_A_DECIMAL, blankToNull, problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["type", "name", "description", "unit", "price_ex_vat", "price_inc_vat", "vat_rate", "active", "sku", "track_stock", "low_stock_threshold", "opening_stock"] as const;

const PRICE_MODES = [
  { value: "ex", label: "Excluding VAT" },
  { value: "inc", label: "Including VAT" },
];

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
  /** Whether `price` is typed excl. or incl. VAT; the backend turns a price incl. VAT into the stored price excl. */
  price_mode: "ex" | "inc";
  price: string;
  vat_rate: string;
  active: boolean;
  sku: string;
  track_stock: boolean;
  low_stock_threshold: string;
  /** New items only: what is on the shelf now (recorded as the opening count once the item exists). */
  opening_stock: string;
}

function toState(item?: Item): FormState {
  return {
    type: item?.type ?? "service",
    name: item?.name ?? "",
    description: item?.description ?? "",
    unit: item?.unit ?? "",
    price_mode: "ex",
    price: item?.price_ex_vat ?? "",
    vat_rate: item?.vat_rate ?? "",
    active: item?.active ?? true,
    sku: item?.sku ?? "",
    track_stock: item?.track_stock ?? false,
    low_stock_threshold: item?.low_stock_threshold ?? "",
    opening_stock: "",
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
  const priceField = state.price_mode === "inc" ? "price_inc_vat" : "price_ex_vat";
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((current) => ({ ...current, [key]: value }));

  /** The two decimal fields as typed, or the shape errors that stop the request. */
  function decimals() {
    const price = parseMoney(state.price.trim());
    const vat = parsePercent(state.vat_rate.trim());
    if (price !== null && vat !== null) return { ok: true as const, price, vat };
    const errors: FieldErrors = {};
    if (price === null) errors[priceField] = [NOT_A_DECIMAL];
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
      [priceField]: parsed.price,
      vat_rate: parsed.vat,
      active: state.active,
      sku: blankToNull(state.sku),
      track_stock: state.type === "product" && state.track_stock,
      ...(state.type === "product" && state.track_stock && state.low_stock_threshold.trim() !== ""
        ? { low_stock_threshold: state.low_stock_threshold.trim() as QuantityString }
        : {}),
    };
    const created = await run(() => apiFetch<Item>(orgId, "/items", { method: "POST", body }));
    if (created === null) return;
    // The stock ledger belongs to Inventory, so the opening count is its own request once the item exists. If it is
    // refused, the item still exists: its page says so and offers the stock form.
    const opening = state.opening_stock.trim();
    let stockFailed = false;
    if (created.track_stock && opening !== "") {
      const counted = await apiFetch(orgId, `/items/${created.id}/stock`, { method: "POST", body: { kind: "count", quantity: opening, note: "Opening stock" } });
      stockFailed = !counted.ok;
    }
    router.push(`/o/${orgId}/catalog/${created.id}?created=1${stockFailed ? "&stock=failed" : ""}`);
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
    if (parsed.vat !== current.vat_rate) body.vat_rate = parsed.vat;
    // Incl. VAT, the typed amount is what counts, so a new VAT rate resends it (and the price excl. VAT follows).
    if (state.price_mode === "inc" && (parsed.price !== current.price_inc_vat || body.vat_rate)) body.price_inc_vat = parsed.price;
    if (state.price_mode === "ex" && parsed.price !== current.price_ex_vat) body.price_ex_vat = parsed.price;
    if (blankToNull(state.sku) !== current.sku) body.sku = blankToNull(state.sku);
    const trackStock = state.type === "product" && state.track_stock;
    if (trackStock !== current.track_stock) body.track_stock = trackStock;
    const threshold = state.low_stock_threshold.trim() === "" ? null : (state.low_stock_threshold.trim() as QuantityString);
    if (trackStock && threshold !== current.low_stock_threshold) body.low_stock_threshold = threshold;
    if (Object.keys(body).length === 0) {
      setNotice("unchanged");
      return;
    }
    const saved = await run(() => apiFetch<Item>(orgId, `/items/${current.id}`, { method: "PATCH", body }));
    if (saved === null) return;
    setRecord(saved);
    // What the backend stored, exactly as it formatted it, in the mode the person chose.
    setState({ ...toState(saved), price_mode: state.price_mode, price: state.price_mode === "inc" ? saved.price_inc_vat : saved.price_ex_vat });
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
        <SelectField
          label="Price is entered"
          name="price_mode"
          value={state.price_mode}
          onChange={(value) => {
            const mode = value as FormState["price_mode"];
            // A saved item shows its stored price in the chosen form; a new one keeps what was typed.
            setState((current) => ({ ...current, price_mode: mode, price: record ? (mode === "inc" ? record.price_inc_vat : record.price_ex_vat) : current.price }));
          }}
          options={PRICE_MODES}
        />
        <DecimalField
          label={state.price_mode === "inc" ? "Price including VAT" : "Price excluding VAT"}
          name={priceField}
          value={state.price}
          onChange={(value) => set("price", value)}
          error={errorsFor(priceField)}
          hint={
            record
              ? `Saved: ${record.price_ex_vat} excl. VAT, ${record.price_inc_vat} incl. VAT.`
              : state.price_mode === "inc"
                ? "Stored as the nearest price excluding VAT; the item page shows both."
                : undefined
          }
        />
        <DecimalField label="VAT rate (%)" name="vat_rate" value={state.vat_rate} onChange={(value) => set("vat_rate", value)} error={errorsFor("vat_rate")} />
        {state.type === "product" && (
          <CheckboxField label="Track stock" name="track_stock" checked={state.track_stock} onChange={(checked) => set("track_stock", checked)} error={errorsFor("track_stock")} />
        )}
        {state.type === "product" && state.track_stock && (
          <DecimalField
            label="Low-stock threshold (optional)"
            name="low_stock_threshold"
            value={state.low_stock_threshold}
            onChange={(value) => set("low_stock_threshold", value)}
            error={errorsFor("low_stock_threshold")}
            hint="Below this quantity on hand the product is shown as low stock."
          />
        )}
        {!record && state.type === "product" && state.track_stock && (
          <DecimalField
            label="On hand now (optional)"
            name="opening_stock"
            value={state.opening_stock}
            onChange={(value) => set("opening_stock", value)}
            error={errorsFor("opening_stock")}
            hint="Recorded as the opening stock. Later changes are made in the item's Stock section."
          />
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
