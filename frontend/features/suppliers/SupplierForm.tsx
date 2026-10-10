"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { PROFILE_CONTROLS, ProfileFields } from "@/components/profile/ProfileFields";
import { ActiveToggle } from "@/components/ui/ActiveToggle";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { CheckboxField, TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";
import type { ProfileField, Supplier, SupplierCreate, SupplierUpdate } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";
import { profileBody, profileChanges, profileState, type ProfileState } from "@/lib/profile";

const TEXT_FIELDS = ["contact_person", "email", "phone", "our_customer_number"] as const;
const CONTROLS = ["name", ...TEXT_FIELDS, "active", ...PROFILE_CONTROLS] as const;

type SupplierText = (typeof TEXT_FIELDS)[number];

interface FormState {
  name: string;
  contact_person: string;
  email: string;
  phone: string;
  our_customer_number: string;
  active: boolean;
  profile: ProfileState;
}

function toState(supplier?: Supplier): FormState {
  return {
    name: supplier?.name ?? "",
    contact_person: supplier?.contact_person ?? "",
    email: supplier?.email ?? "",
    phone: supplier?.phone ?? "",
    our_customer_number: supplier?.our_customer_number ?? "",
    active: supplier?.active ?? true,
    profile: profileState(supplier),
  };
}

/**
 * Create (no `supplier`) or edit (`supplier` = the saved record), like the customer form. The backend decides what is
 * acceptable; its 422 answers are shown on the matching control. There is no organization field.
 */
export function SupplierForm({ supplier }: { supplier?: Supplier }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [record, setRecord] = useState<Supplier | undefined>(supplier);
  const [state, setState] = useState<FormState>(() => toState(supplier));
  const [notice, setNotice] = useState<"saved" | "unchanged" | null>(null);

  const problems = problemsFrom(error, CONTROLS);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((current) => ({ ...current, [key]: value }));
  const setProfile = (field: ProfileField, value: string) => setState((current) => ({ ...current, profile: { ...current.profile, [field]: value } }));

  async function create() {
    const body: SupplierCreate = {
      name: state.name,
      contact_person: blankToNull(state.contact_person),
      email: blankToNull(state.email),
      phone: blankToNull(state.phone),
      our_customer_number: blankToNull(state.our_customer_number),
      active: state.active,
      ...profileBody(state.profile),
    };
    const created = await run(() => apiFetch<Supplier>(orgId, "/suppliers", { method: "POST", body }));
    if (created === null) return;
    router.push(`/o/${orgId}/suppliers/${created.id}?created=1`);
    router.refresh();
  }

  async function save(current: Supplier) {
    // Only what changed is sent, so an edit never overwrites fields it did not touch.
    const body: SupplierUpdate = {};
    if (state.name !== current.name) body.name = state.name;
    for (const field of TEXT_FIELDS) {
      if (blankToNull(state[field]) !== current[field]) body[field] = blankToNull(state[field]);
    }
    Object.assign(body, profileChanges(state.profile, current));
    if (Object.keys(body).length === 0) {
      setNotice("unchanged");
      return;
    }
    const saved = await run(() => apiFetch<Supplier>(orgId, `/suppliers/${current.id}`, { method: "PATCH", body }));
    if (saved === null) return;
    setRecord(saved);
    setState(toState(saved));
    setNotice("saved");
    router.refresh();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    void (record ? save(record) : create());
  }

  const text = (field: SupplierText, label: string, extra: { inputMode?: "email" | "tel"; hint?: string } = {}) => (
    <TextField label={label} name={field} value={state[field]} onChange={(value) => set(field, value)} error={problems.byField[field]} autoComplete="off" {...extra} />
  );

  return (
    <div className="flex max-w-xl flex-col gap-4">
      {record && (
        <ActiveToggle<Supplier>
          path={`/suppliers/${record.id}`}
          active={record.active}
          noun="supplier"
          onChanged={(updated) => setRecord((current) => (current ? { ...current, active: updated.active, updated_at: updated.updated_at } : updated))}
        />
      )}
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4" aria-label={record ? "Edit supplier" : "New supplier"}>
        <TextField label="Name" name="name" value={state.name} onChange={(value) => set("name", value)} error={problems.byField.name} autoComplete="off" />
        {text("contact_person", "Contact person")}
        {text("email", "Email", { inputMode: "email" })}
        {text("phone", "Phone", { inputMode: "tel" })}
        {text("our_customer_number", "Our customer number", { hint: "Your customer number at this supplier, to quote when ordering." })}
        <fieldset className="flex flex-col gap-4">
          <legend className="pb-1 text-sm font-medium">Address and identifiers</legend>
          <ProfileFields state={state.profile} onChange={setProfile} errors={problems.byField} />
        </fieldset>
        {!record && <CheckboxField label="Active" name="active" checked={state.active} onChange={(checked) => set("active", checked)} error={problems.byField.active} />}
        <ErrorSummary messages={problems.general} />
        {notice === "saved" && <Notice testId="saved">Saved.</Notice>}
        {notice === "unchanged" && <Notice testId="unchanged">No changes to save.</Notice>}
        <div className="flex items-center gap-4">
          <Button type="submit" disabled={pending} data-testid="submit">
            {pending ? "Saving…" : record ? "Save changes" : "Create supplier"}
          </Button>
          <Link href={`/o/${orgId}/suppliers`} className="text-sm underline">
            {record ? "Back to suppliers" : "Cancel"}
          </Link>
        </div>
      </form>
    </div>
  );
}
