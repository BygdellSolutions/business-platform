"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { ActiveToggle } from "@/components/ui/ActiveToggle";
import { Button } from "@/components/ui/Button";
import { EntityPicker, type PickerEntity } from "@/components/ui/EntityPicker";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { CheckboxField, SelectField, TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { customerEntity, customerSearch } from "@/features/customers/customer-picker";
import { apiFetch } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/errors";
import type { Horse, HorseCreate, HorseSex, HorseUpdate } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["name", "owner_customer_id", "stable_customer_id", "birth_year", "sex", "breed", "active"] as const;

const SEXES = [
  { value: "", label: "Not set" },
  { value: "mare", label: "Mare" },
  { value: "stallion", label: "Stallion" },
  { value: "gelding", label: "Gelding" },
];

const NOT_A_YEAR = "Enter a whole number, such as 2012.";

interface FormState {
  name: string;
  owner: PickerEntity | null;
  stable: PickerEntity | null;
  birth_year: string;
  sex: "" | HorseSex;
  breed: string;
  active: boolean;
}

function toState(horse?: Horse): FormState {
  return {
    name: horse?.name ?? "",
    owner: horse ? customerEntity(horse.owner) : null,
    stable: horse?.stable ? customerEntity(horse.stable) : null,
    birth_year: horse?.birth_year === null || horse === undefined ? "" : String(horse.birth_year),
    sex: horse?.sex ?? "",
    breed: horse?.breed ?? "",
    active: horse?.active ?? true,
  };
}

/**
 * The backend wants a JSON integer for a birth year (a string is refused). Only the shape is
 * checked here; whether the year is plausible is the backend's decision.
 */
function wholeNumber(text: string): number | null | "invalid" {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const number = /^\d+$/.test(trimmed) ? Number(trimmed) : NaN;
  return Number.isSafeInteger(number) ? number : "invalid";
}

/**
 * Create (no `horse`) or edit (`horse` = the saved record). Owner and Stable are customers,
 * chosen with the entity picker; what is submitted is each customer's id. The backend decides
 * whether those ids are customers of this organization and whether they may be assigned (an
 * inactive customer may not be NEWLY assigned, but one already assigned stays valid, so an
 * edit never sends a reference that did not change).
 */
export function HorseForm({ horse }: { horse?: Horse }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [record, setRecord] = useState<Horse | undefined>(horse);
  const [state, setState] = useState<FormState>(() => toState(horse));
  const [local, setLocal] = useState<FieldErrors>({});
  const [notice, setNotice] = useState<"saved" | "unchanged" | null>(null);

  // One search function per organization; the picker drops every result when it changes.
  const search = useMemo(() => customerSearch(orgId, { activeOnly: true }), [orgId]);

  const problems = problemsFrom(error, CONTROLS);
  const errorsFor = (name: string) => local[name] ?? problems.byField[name];
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((current) => ({ ...current, [key]: value }));

  async function create() {
    const year = wholeNumber(state.birth_year);
    if (year === "invalid") {
      setLocal({ birth_year: [NOT_A_YEAR] });
      return;
    }
    const body: HorseCreate = {
      name: state.name,
      // No owner chosen: leave the field out, and the backend says it is required.
      ...(state.owner ? { owner_customer_id: state.owner.id } : {}),
      stable_customer_id: state.stable?.id ?? null,
      birth_year: year,
      sex: state.sex === "" ? null : state.sex,
      breed: blankToNull(state.breed),
      active: state.active,
    };
    const created = await run(() => apiFetch<Horse>(orgId, "/horses", { method: "POST", body }));
    if (created === null) return;
    router.push(`/o/${orgId}/horses/${created.id}?created=1`);
    router.refresh(); // drop cached pages (a list visited before) so Back does not show them without the new horse
  }

  async function save(current: Horse) {
    const year = wholeNumber(state.birth_year);
    if (year === "invalid") {
      setLocal({ birth_year: [NOT_A_YEAR] });
      return;
    }
    // Only what changed is sent. An unchanged owner or stable is NOT sent, so a customer that
    // was deactivated after being assigned never blocks an edit of another field.
    const body: HorseUpdate = {};
    if (state.name !== current.name) body.name = state.name;
    if (state.owner && state.owner.id !== current.owner_customer_id) body.owner_customer_id = state.owner.id;
    if ((state.stable?.id ?? null) !== current.stable_customer_id) body.stable_customer_id = state.stable?.id ?? null;
    if (year !== current.birth_year) body.birth_year = year;
    if ((state.sex === "" ? null : state.sex) !== current.sex) body.sex = state.sex === "" ? null : state.sex;
    if (blankToNull(state.breed) !== current.breed) body.breed = blankToNull(state.breed);
    if (Object.keys(body).length === 0) {
      setNotice("unchanged");
      return;
    }
    const saved = await run(() => apiFetch<Horse>(orgId, `/horses/${current.id}`, { method: "PATCH", body }));
    if (saved === null) return;
    setRecord(saved);
    setState(toState(saved)); // what the backend stored, including who the owner and stable are now
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
        <ActiveToggle<Horse>
          path={`/horses/${record.id}`}
          active={record.active}
          noun="horse"
          onChanged={(updated) => setRecord((current) => (current ? { ...current, active: updated.active, updated_at: updated.updated_at } : updated))}
        />
      )}
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4" aria-label={record ? "Edit horse" : "New horse"}>
        <TextField label="Name" name="name" value={state.name} onChange={(value) => set("name", value)} error={errorsFor("name")} autoComplete="off" />
        <EntityPicker label="Owner" name="owner_customer_id" value={state.owner} onChange={(owner) => set("owner", owner)} search={search} error={errorsFor("owner_customer_id")} />
        <EntityPicker
          label="Stable"
          name="stable_customer_id"
          value={state.stable}
          onChange={(stable) => set("stable", stable)}
          search={search}
          clearable
          error={errorsFor("stable_customer_id")}
          hint="Optional."
        />
        <TextField label="Birth year" name="birth_year" value={state.birth_year} onChange={(value) => set("birth_year", value)} error={errorsFor("birth_year")} inputMode="text" autoComplete="off" />
        <SelectField label="Sex" name="sex" value={state.sex} onChange={(value) => set("sex", value as "" | HorseSex)} options={SEXES} error={errorsFor("sex")} />
        <TextField label="Breed" name="breed" value={state.breed} onChange={(value) => set("breed", value)} error={errorsFor("breed")} autoComplete="off" />
        {!record && <CheckboxField label="Active" name="active" checked={state.active} onChange={(checked) => set("active", checked)} error={errorsFor("active")} />}
        <ErrorSummary messages={problems.general} />
        {notice === "saved" && <Notice testId="saved">Saved.</Notice>}
        {notice === "unchanged" && <Notice testId="unchanged">No changes to save.</Notice>}
        <div className="flex items-center gap-4">
          <Button type="submit" disabled={pending} data-testid="submit">
            {pending ? "Saving…" : record ? "Save changes" : "Create horse"}
          </Button>
          <Link href={`/o/${orgId}/horses`} className="text-sm underline">
            {record ? "Back to horses" : "Cancel"}
          </Link>
        </div>
      </form>
    </div>
  );
}
