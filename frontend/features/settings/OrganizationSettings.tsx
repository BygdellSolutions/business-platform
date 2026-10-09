"use client";

import { useState, useSyncExternalStore, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { PROFILE_CONTROLS, ProfileFields } from "@/components/profile/ProfileFields";
import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";
import type { Organization, OrganizationUpdate, ProfileField } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";
import { PROFILE_FIELDS, PROFILE_LABELS, profileChanges, profileState, type ProfileState } from "@/lib/profile";

import { SELLER_CONTROLS, SellerFields, sellerChanges, sellerRows, sellerState, type SellerState } from "./SellerFields";

const CONTROLS = ["name", "legal_name", "default_currency", "timezone", ...PROFILE_CONTROLS, ...SELLER_CONTROLS] as const;

// Zone names the browser knows, offered as suggestions (the backend decides what is valid). Read only in the
// browser: the server renders none, so the lists can never differ between the server's and the browser's render.
const NO_ZONES: readonly string[] = [];
let browserZones: readonly string[] | undefined;
const zoneSuggestions = () => (browserZones ??= typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : NO_ZONES);
const noSubscription = () => () => {};

/**
 * The organization's settings. An owner or admin gets the form; everyone else sees the same
 * values read-only. (Hiding the form is a convenience: FastAPI refuses the change for any other
 * role, and the form's own errors show that answer.)
 */
export function OrganizationSettings({ organization, canEdit }: { organization: Organization; canEdit: boolean }) {
  return canEdit ? <SettingsForm organization={organization} /> : <ReadOnlySettings organization={organization} />;
}

function ReadOnlySettings({ organization }: { organization: Organization }) {
  const rows: [string, string, string | null][] = [
    ["name", "Name", organization.name],
    ["legal_name", "Legal name", organization.legal_name],
    ["default_currency", "Default currency", organization.default_currency],
    ["timezone", "Time zone", organization.timezone],
    ...PROFILE_FIELDS.map((field): [string, string, string | null] => [field, PROFILE_LABELS[field], organization[field]]),
    ...sellerRows(organization),
  ];
  return (
    <div className="flex max-w-xl flex-col gap-4">
      <Notice testId="read-only">Only an owner or admin can change these settings.</Notice>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm" aria-label="Organization settings">
        {rows.map(([name, label, value]) => (
          <div key={name} className="contents">
            <dt className="font-medium">{label}</dt>
            <dd data-testid={`setting-${name}`}>{value ?? <span className="text-zinc-500">Not set</span>}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

interface FormState {
  name: string;
  legal_name: string;
  default_currency: string;
  timezone: string;
  profile: ProfileState;
  seller: SellerState;
}

function toState(organization: Organization): FormState {
  return {
    name: organization.name,
    legal_name: organization.legal_name ?? "",
    default_currency: organization.default_currency ?? "",
    timezone: organization.timezone ?? "",
    profile: profileState(organization),
    seller: sellerState(organization),
  };
}

function currencyHint(organization: Organization): string {
  if (organization.default_currency === null) {
    return "Not set yet. Orders cannot be created until a currency is set.";
  }
  if (organization.default_currency_locked) {
    return `Fixed: it can no longer be changed because prices already exist. ${organization.default_currency_lock_reason ?? ""}`.trim();
  }
  return "It can be changed only until the first item or order exists.";
}

function timezoneHint(organization: Organization): string {
  if (organization.timezone === null) return `Not set: new dates default to today in UTC (${organization.today}). For example Europe/Stockholm.`;
  return `New dates default to today in this time zone (${organization.today}).`;
}

function SettingsForm({ organization }: { organization: Organization }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [record, setRecord] = useState(organization);
  const [state, setState] = useState<FormState>(() => toState(organization));
  const [notice, setNotice] = useState<"saved" | "unchanged" | null>(null);

  const zones = useSyncExternalStore(noSubscription, zoneSuggestions, () => NO_ZONES);
  const problems = problemsFrom(error, CONTROLS);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((current) => ({ ...current, [key]: value }));
  const setProfile = (field: ProfileField, value: string) => setState((current) => ({ ...current, profile: { ...current.profile, [field]: value } }));

  async function save() {
    // Only what changed is sent, so a save never overwrites a field it did not touch.
    const body: OrganizationUpdate = {};
    if (state.name !== record.name) body.name = state.name;
    if (blankToNull(state.legal_name) !== record.legal_name) body.legal_name = blankToNull(state.legal_name);
    if (state.default_currency.trim() !== (record.default_currency ?? "")) body.default_currency = state.default_currency.trim();
    if (blankToNull(state.timezone.trim()) !== record.timezone) body.timezone = blankToNull(state.timezone.trim());
    Object.assign(body, profileChanges(state.profile, record), sellerChanges(state.seller, record));
    if (Object.keys(body).length === 0) {
      setNotice("unchanged");
      return;
    }
    const saved = await run(() => apiFetch<Organization>(orgId, "/organization", { method: "PATCH", body }));
    if (saved === null) return;
    setRecord(saved);
    setState(toState(saved)); // show what the backend stored (trimmed, upper-cased codes)
    setNotice("saved");
    router.refresh(); // the lock state, the shell's organization name and other pages follow the new settings
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    void save();
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex max-w-xl flex-col gap-4" aria-label="Organization settings">
      <TextField label="Name" name="name" value={state.name} onChange={(value) => set("name", value)} error={problems.byField.name} autoComplete="off" hint="Shown in the app." />
      <TextField label="Legal name" name="legal_name" value={state.legal_name} onChange={(value) => set("legal_name", value)} error={problems.byField.legal_name} autoComplete="off" hint="For documents, if different from the name." />
      <TextField
        label="Default currency"
        name="default_currency"
        value={state.default_currency}
        onChange={(value) => set("default_currency", value)}
        error={problems.byField.default_currency}
        disabled={record.default_currency_locked}
        autoComplete="off"
        hint={currencyHint(record)}
      />
      <TextField
        label="Time zone"
        name="timezone"
        value={state.timezone}
        onChange={(value) => set("timezone", value)}
        error={problems.byField.timezone}
        autoComplete="off"
        list="timezone-suggestions"
        hint={timezoneHint(record)}
      />
      <datalist id="timezone-suggestions">
        {zones.map((zone) => (
          <option key={zone} value={zone} />
        ))}
      </datalist>
      <fieldset className="flex flex-col gap-4">
        <legend className="pb-1 text-sm font-medium">Business profile</legend>
        <ProfileFields state={state.profile} onChange={setProfile} errors={problems.byField} />
      </fieldset>
      <SellerFields state={state.seller} onChange={(seller) => set("seller", seller)} errors={problems.byField} />
      <ErrorSummary messages={problems.general} />
      {notice === "saved" && <Notice testId="saved">Saved.</Notice>}
      {notice === "unchanged" && <Notice testId="unchanged">No changes to save.</Notice>}
      <div>
        <Button type="submit" disabled={pending} data-testid="submit">
          {pending ? "Saving…" : "Save settings"}
        </Button>
      </div>
    </form>
  );
}
