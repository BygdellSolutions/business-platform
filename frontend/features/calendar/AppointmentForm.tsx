"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { EntityPicker, type PickerEntity, type PickerSearch } from "@/components/ui/EntityPicker";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { FieldShell, SelectField, TextAreaField, TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { customerSearch } from "@/features/customers/customer-picker";
import { apiFetch } from "@/lib/api/client";
import type { Appointment, AppointmentCreate, AppointmentUpdate, Colleague, Horse } from "@/lib/api/types";
import { calendarHref } from "@/features/calendar/Calendar";
import { localParts, minutesOf, timeText, zonedInstant } from "@/lib/calendar";
import { addDays } from "@/lib/dates";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["title", "starts_at", "ends_at", "location", "notes", "practitioner_user_id", "customer_id", "subject_id"] as const;
const SUBJECT_TYPE = "horse";
const NOBODY = "";
const DEFAULT_MINUTES = 60;

export interface Prefill {
  day: string;
  /** "HH:mm" the booking starts at, or null for a default morning. */
  start: string | null;
  /** The person it is booked for, if the calendar was narrowed to one. */
  who: string | null;
}

interface FormState {
  title: string;
  startDay: string;
  startTime: string;
  endDay: string;
  endTime: string;
  practitioner: string;
  customer: PickerEntity | null;
  horse: PickerEntity | null;
  location: string;
  notes: string;
}

function toState(zone: string | null, prefill: Prefill, appointment?: Appointment): FormState {
  if (appointment) {
    const start = localParts(appointment.starts_at, zone);
    const end = localParts(appointment.ends_at, zone);
    return {
      title: appointment.title,
      startDay: start.day,
      startTime: timeText(start.minutes),
      endDay: end.day,
      endTime: timeText(end.minutes),
      practitioner: appointment.practitioner_user_id ?? NOBODY,
      customer: appointment.customer_id ? { id: appointment.customer_id, label: appointment.customer_name ?? "Customer" } : null,
      horse: appointment.subject_type === SUBJECT_TYPE && appointment.subject_id ? { id: appointment.subject_id, label: appointment.subject_label ?? "Horse" } : null,
      location: appointment.location ?? "",
      notes: appointment.notes ?? "",
    };
  }
  const startMinutes = (prefill.start ? minutesOf(prefill.start) : null) ?? 9 * 60;
  const endMinutes = startMinutes + DEFAULT_MINUTES;
  return {
    title: "",
    startDay: prefill.day,
    startTime: timeText(startMinutes),
    endDay: endMinutes >= 24 * 60 ? addDays(prefill.day, 1) : prefill.day,
    endTime: timeText(endMinutes % (24 * 60)),
    practitioner: prefill.who ?? NOBODY,
    customer: null,
    horse: null,
    location: "",
    notes: "",
  };
}

function horseSearch(orgId: string): PickerSearch {
  return async (query, signal) => {
    const params = new URLSearchParams({ limit: "20", active: "true" });
    if (query !== "") params.set("q", query);
    const result = await apiFetch<Horse[]>(orgId, `/horses?${params}`, { signal });
    if (!result.ok) return result;
    return { ok: true, status: result.status, data: result.data.map((horse) => ({ id: horse.id, label: horse.name, inactive: !horse.active })) };
  };
}

/**
 * Book (no `appointment`) or change one. Times are typed as the organization's wall clock and sent as instants, so the booking
 * means the same moment to everyone whatever their own computer's time zone. A change is saved against the version it was opened on.
 */
export function AppointmentForm({
  appointment,
  zone,
  people,
  prefill,
  today,
}: {
  appointment?: Appointment;
  zone: string | null;
  people: Colleague[];
  prefill: Prefill;
  today: string;
}) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [record, setRecord] = useState<Appointment | undefined>(appointment);
  const [state, setState] = useState<FormState>(() => toState(zone, prefill, appointment));
  const [local, setLocal] = useState<string[]>([]);
  const [notice, setNotice] = useState<"saved" | "unchanged" | null>(null);

  const problems = problemsFrom(error, CONTROLS);
  const stale = error?.kind === "conflict" && error.code === "stale_record";
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((current) => ({ ...current, [key]: value }));
  const customers = customerSearch(orgId, { activeOnly: true });
  const horses = horseSearch(orgId);
  const backToCalendar = calendarHref(orgId, "week", state.startDay, null);

  function instants(): { starts_at: string; ends_at: string } | null {
    const problemsFound: string[] = [];
    for (const [label, time] of [["start", state.startTime], ["end", state.endTime]] as const) {
      if (minutesOf(time) === null) problemsFound.push(`Enter the ${label} time as hours and minutes, for example 09:30.`);
    }
    for (const [label, day] of [["start", state.startDay], ["end", state.endDay]] as const) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) problemsFound.push(`Choose the ${label} date.`);
    }
    if (problemsFound.length > 0) {
      setLocal(problemsFound);
      return null;
    }
    const starts_at = zonedInstant(state.startDay, state.startTime, zone);
    const ends_at = zonedInstant(state.endDay, state.endTime, zone);
    if (new Date(ends_at) <= new Date(starts_at)) {
      setLocal(["The appointment must end after it starts."]);
      return null;
    }
    setLocal([]);
    return { starts_at, ends_at };
  }

  const subject = () => (state.horse ? { subject_type: SUBJECT_TYPE, subject_id: state.horse.id } : { subject_type: null, subject_id: null });

  async function create() {
    const when = instants();
    if (when === null) return;
    const body: AppointmentCreate = {
      title: state.title,
      ...when,
      location: blankToNull(state.location),
      notes: blankToNull(state.notes),
      practitioner_user_id: state.practitioner === NOBODY ? null : state.practitioner,
      customer_id: state.customer?.id ?? null,
      ...subject(),
    };
    const created = await run(() => apiFetch<Appointment>(orgId, "/appointments", { method: "POST", body }));
    if (created === null) return;
    router.push(calendarHref(orgId, "week", state.startDay, null));
    router.refresh();
  }

  async function save(current: Appointment) {
    const when = instants();
    if (when === null) return;
    const body: AppointmentUpdate = {};
    if (state.title !== current.title) body.title = state.title;
    if (new Date(when.starts_at).getTime() !== new Date(current.starts_at).getTime()) body.starts_at = when.starts_at;
    if (new Date(when.ends_at).getTime() !== new Date(current.ends_at).getTime()) body.ends_at = when.ends_at;
    if (blankToNull(state.location) !== current.location) body.location = blankToNull(state.location);
    if (blankToNull(state.notes) !== current.notes) body.notes = blankToNull(state.notes);
    const practitioner = state.practitioner === NOBODY ? null : state.practitioner;
    if (practitioner !== current.practitioner_user_id) body.practitioner_user_id = practitioner;
    if ((state.customer?.id ?? null) !== current.customer_id) body.customer_id = state.customer?.id ?? null;
    if ((state.horse?.id ?? null) !== (current.subject_type === SUBJECT_TYPE ? current.subject_id : null)) Object.assign(body, subject());
    if (Object.keys(body).length === 0) {
      setNotice("unchanged");
      return;
    }
    await change(current, body);
  }

  async function change(current: Appointment, body: AppointmentUpdate) {
    const saved = await run(() => apiFetch<Appointment>(orgId, `/appointments/${current.id}`, { method: "PATCH", body, ifMatch: current.version }));
    if (saved === null) return;
    setRecord(saved);
    setState(toState(zone, prefill, saved));
    setNotice("saved");
    router.refresh();
  }

  async function remove(current: Appointment) {
    const gone = await run(() => apiFetch<null>(orgId, `/appointments/${current.id}`, { method: "DELETE", ifMatch: current.version }));
    if (gone === null) return;
    router.push(backToCalendar);
    router.refresh();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    void (record ? save(record) : create());
  }

  const cancelled = record?.status === "cancelled";
  const personOptions = [{ value: NOBODY, label: "Nobody in particular" }, ...people.map((person) => ({ value: person.user_id, label: person.name }))];
  if (state.practitioner !== NOBODY && !people.some((person) => person.user_id === state.practitioner)) {
    personOptions.push({ value: state.practitioner, label: record?.practitioner_name ?? "Former colleague" });
  }

  return (
    <div className="flex max-w-xl flex-col gap-4">
      {cancelled && <Notice testId="cancelled">This appointment is cancelled. It stays on record but is not shown in the calendar.</Notice>}
      <form onSubmit={onSubmit} noValidate className="panel flex max-w-3xl flex-col gap-4" aria-label={record ? "Edit appointment" : "New appointment"}>
        <TextField label="Title" name="title" value={state.title} onChange={(value) => set("title", value)} error={problems.byField.title} autoComplete="off" />
        <div className="grid grid-cols-2 gap-3">
          <FieldShell label="Starts" name="starts_at" error={problems.byField.starts_at}>
            {(control) => <input {...control} type="date" value={state.startDay} onChange={(event) => set("startDay", event.target.value)} className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />}
          </FieldShell>
          <FieldShell label="Start time" name="start_time">
            {(control) => <input {...control} type="time" step={300} value={state.startTime} onChange={(event) => set("startTime", event.target.value)} className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />}
          </FieldShell>
          <FieldShell label="Ends" name="ends_at" error={problems.byField.ends_at}>
            {(control) => <input {...control} type="date" value={state.endDay} onChange={(event) => set("endDay", event.target.value)} className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />}
          </FieldShell>
          <FieldShell label="End time" name="end_time">
            {(control) => <input {...control} type="time" step={300} value={state.endTime} onChange={(event) => set("endTime", event.target.value)} className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />}
          </FieldShell>
        </div>
        <p className="text-xs text-zinc-500">Times are in the organization&apos;s time zone ({zone ?? "UTC"}).</p>
        <SelectField label="Practitioner" name="practitioner_user_id" value={state.practitioner} onChange={(value) => set("practitioner", value)} options={personOptions} error={problems.byField.practitioner_user_id} hint="Who does the work." />
        <EntityPicker label="Customer" name="customer_id" value={state.customer} onChange={(customer) => set("customer", customer)} search={customers} clearable error={problems.byField.customer_id} hint="Optional." />
        <EntityPicker label="Horse" name="subject_id" value={state.horse} onChange={(horse) => set("horse", horse)} search={horses} clearable error={problems.byField.subject_id} hint="Optional." />
        <TextField label="Location" name="location" value={state.location} onChange={(value) => set("location", value)} error={problems.byField.location} autoComplete="off" />
        <TextAreaField label="Notes" name="notes" rows={4} value={state.notes} onChange={(value) => set("notes", value)} error={problems.byField.notes} />
        <ErrorSummary messages={[...local, ...(error?.kind === "validation" ? problems.general : [])]} />
        {stale && (
          <div role="alert" data-testid="stale" className="flex flex-col gap-2 rounded border border-amber-400 p-3 text-sm">
            <p>This appointment was changed somewhere else, so nothing was saved. Reload to see the latest version (your unsaved changes here are lost).</p>
            <div>
              <Button type="button" onClick={() => router.refresh()}>
                Reload
              </Button>
            </div>
          </div>
        )}
        {error && !stale && error.kind !== "validation" && <p role="alert">{error.message}</p>}
        {notice === "saved" && <Notice testId="saved">Saved.</Notice>}
        {notice === "unchanged" && <Notice testId="unchanged">No changes to save.</Notice>}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={pending} data-testid="submit">
            {pending ? "Saving…" : record ? "Save changes" : "Book appointment"}
          </Button>
          <Button type="button" onClick={() => router.push(record ? backToCalendar : calendarHref(orgId, "week", prefill.day || today, null))}>
            {record ? "Back to the calendar" : "Cancel"}
          </Button>
        </div>
      </form>
      {record && (
        <div className="flex flex-wrap items-center gap-3" data-testid="appointment-actions">
          {cancelled ? (
            <Button type="button" disabled={pending} onClick={() => void change(record, { status: "scheduled" })} data-testid="restore">
              Restore this appointment
            </Button>
          ) : (
            <ConfirmButton
              label="Cancel appointment"
              question="Cancel this appointment? It stays on record."
              confirmLabel="Yes, cancel it"
              declineLabel="No, keep it booked"
              disabled={pending}
              onConfirm={() => void change(record, { status: "cancelled" })}
              testId="cancel-appointment"
            />
          )}
          <ConfirmButton
            label="Delete"
            question="Delete this appointment for good?"
            confirmLabel="Yes, delete it"
            declineLabel="No, go back"
            disabled={pending}
            onConfirm={() => void remove(record)}
            testId="delete-appointment"
          />
        </div>
      )}
    </div>
  );
}
