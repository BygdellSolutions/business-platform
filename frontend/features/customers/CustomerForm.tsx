"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { ActiveToggle } from "@/components/ui/ActiveToggle";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { CheckboxField, DecimalField, SelectField, TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { PROFILE_CONTROLS, ProfileFields } from "@/components/profile/ProfileFields";
import { apiFetch } from "@/lib/api/client";
import type { Customer, CustomerCreate, CustomerType, CustomerUpdate } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";
import { profileBody, profileChanges, profileState, type ProfileState } from "@/lib/profile";
import type { ProfileField } from "@/lib/api/types";

const CONTROLS = ["customer_type", "name", "email", "phone", "active", "default_discount_percent", ...PROFILE_CONTROLS] as const;

const TYPES = [
  { value: "person", label: "Person" },
  { value: "company", label: "Company" },
];

interface FormState {
  customer_type: CustomerType;
  name: string;
  email: string;
  phone: string;
  active: boolean;
  discount: string;
  profile: ProfileState;
}

function toState(customer?: Customer): FormState {
  return {
    customer_type: customer?.customer_type ?? "person",
    name: customer?.name ?? "",
    email: customer?.email ?? "",
    phone: customer?.phone ?? "",
    active: customer?.active ?? true,
    discount: customer?.default_discount_percent ?? "",
    profile: profileState(customer),
  };
}

/**
 * Create (no `customer`) or edit (`customer` = the saved record). Whether a name, email or
 * phone is acceptable is decided by the backend; its 422 answers are shown on the matching
 * control. There is no organization field: the BFF and FastAPI take the organization from
 * the URL.
 */
export function CustomerForm({ customer, canSetDiscount = false }: { customer?: Customer; canSetDiscount?: boolean }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [record, setRecord] = useState<Customer | undefined>(customer);
  const [state, setState] = useState<FormState>(() => toState(customer));
  const [notice, setNotice] = useState<"saved" | "unchanged" | null>(null);

  const problems = problemsFrom(error, CONTROLS);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((current) => ({ ...current, [key]: value }));
  const setProfile = (field: ProfileField, value: string) => setState((current) => ({ ...current, profile: { ...current.profile, [field]: value } }));

  async function create() {
    const body: CustomerCreate = {
      customer_type: state.customer_type,
      name: state.name,
      email: blankToNull(state.email),
      phone: blankToNull(state.phone),
      active: state.active,
      ...profileBody(state.profile),
      ...(canSetDiscount ? { default_discount_percent: blankToNull(state.discount) } : {}),
    };
    const created = await run(() => apiFetch<Customer>(orgId, "/customers", { method: "POST", body }));
    if (created === null) return;
    router.push(`/o/${orgId}/customers/${created.id}?created=1`);
    router.refresh(); // drop cached pages (the list visited before) so Back does not show them without the new record
  }

  async function save(current: Customer) {
    // Only what changed is sent, so an edit never overwrites fields it did not touch.
    const body: CustomerUpdate = {};
    if (state.customer_type !== current.customer_type) body.customer_type = state.customer_type;
    if (state.name !== current.name) body.name = state.name;
    if (blankToNull(state.email) !== current.email) body.email = blankToNull(state.email);
    if (blankToNull(state.phone) !== current.phone) body.phone = blankToNull(state.phone);
    Object.assign(body, profileChanges(state.profile, current));
    if (canSetDiscount && blankToNull(state.discount) !== current.default_discount_percent) body.default_discount_percent = blankToNull(state.discount);
    if (Object.keys(body).length === 0) {
      setNotice("unchanged");
      return;
    }
    const saved = await run(() => apiFetch<Customer>(orgId, `/customers/${current.id}`, { method: "PATCH", body }));
    if (saved === null) return;
    setRecord(saved);
    setState(toState(saved)); // show what the backend stored (trimmed, normalized)
    setNotice("saved");
    router.refresh(); // re-render the server components (page title, lists) with the new data
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    void (record ? save(record) : create());
  }

  return (
    <div className="flex max-w-xl flex-col gap-4">
      {record && (
        <ActiveToggle<Customer>
          path={`/customers/${record.id}`}
          active={record.active}
          noun="customer"
          onChanged={(updated) => setRecord((current) => (current ? { ...current, active: updated.active, updated_at: updated.updated_at } : updated))}
        />
      )}
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4" aria-label={record ? "Edit customer" : "New customer"}>
        <SelectField label="Type" name="customer_type" value={state.customer_type} onChange={(value) => set("customer_type", value as CustomerType)} options={TYPES} error={problems.byField.customer_type} />
        <TextField label="Name" name="name" value={state.name} onChange={(value) => set("name", value)} error={problems.byField.name} autoComplete="off" />
        <TextField label="Email" name="email" value={state.email} onChange={(value) => set("email", value)} error={problems.byField.email} inputMode="email" autoComplete="off" />
        <TextField label="Phone" name="phone" value={state.phone} onChange={(value) => set("phone", value)} error={problems.byField.phone} inputMode="tel" autoComplete="off" />
        {canSetDiscount ? (
          <DecimalField
            label="Default discount %"
            name="default_discount_percent"
            value={state.discount}
            onChange={(value) => set("discount", value)}
            error={problems.byField.default_discount_percent}
            hint="Applied to catalog items for this customer, after any campaign discount. Leave empty for none."
          />
        ) : (
          <p className="text-sm" data-testid="discount-read-only">
            Default discount: {record?.default_discount_percent ? `${record.default_discount_percent} %` : "none"}{" "}
            <span className="text-zinc-500">(an owner or admin sets it)</span>
          </p>
        )}
        <fieldset className="flex flex-col gap-4">
          <legend className="pb-1 text-sm font-medium">Billing details</legend>
          <ProfileFields state={state.profile} onChange={setProfile} errors={problems.byField} />
        </fieldset>
        {!record && <CheckboxField label="Active" name="active" checked={state.active} onChange={(checked) => set("active", checked)} error={problems.byField.active} />}
        <ErrorSummary messages={problems.general} />
        {notice === "saved" && <Notice testId="saved">Saved.</Notice>}
        {notice === "unchanged" && <Notice testId="unchanged">No changes to save.</Notice>}
        <div className="flex items-center gap-4">
          <Button type="submit" disabled={pending} data-testid="submit">
            {pending ? "Saving…" : record ? "Save changes" : "Create customer"}
          </Button>
          <Link href={`/o/${orgId}/customers`} className="text-sm underline">
            {record ? "Back to customers" : "Cancel"}
          </Link>
        </div>
      </form>
    </div>
  );
}
