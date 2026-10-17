"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { EntityPicker, type PickerEntity, type PickerSearch } from "@/components/ui/EntityPicker";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { FieldShell, SelectField, TextAreaField, TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { itemSearch } from "@/features/catalog/item-picker";
import { customerSearch } from "@/features/customers/customer-picker";
import { apiFetch } from "@/lib/api/client";
import type { Appointment, AppointmentCreate, AppointmentUpdate, Colleague, Horse, OrderUpdateResult } from "@/lib/api/types";
import { calendarHref } from "@/features/calendar/Calendar";
import { localParts, minutesOf, timeText, zonedInstant } from "@/lib/calendar";
import { addDays } from "@/lib/dates";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["title", "starts_at", "ends_at", "location", "notes", "practitioner_user_id", "customer_id", "subjects", "items"] as const;
const SUBJECT_TYPE = "horse";
// A booking is always for a real person. Empty only while none is chosen (an older booking that had none, or an organization with nobody to choose).
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
  horses: PickerEntity[];
  services: PickerEntity[];
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
      horses: appointment.subjects.filter((subject) => subject.subject_type === SUBJECT_TYPE).map((subject) => ({ id: subject.subject_id, label: subject.label ?? "A horse" })),
      services: appointment.items.map((item) => ({ id: item.item_id, label: item.name ?? "A service" })),
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
    horses: [],
    services: [],
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
  const [orderNotice, setOrderNotice] = useState<string | null>(null);

  const problems = problemsFrom(error, CONTROLS);
  const stale = error?.kind === "conflict" && error.code === "stale_record";
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((current) => ({ ...current, [key]: value }));
  const customers = customerSearch(orgId, { activeOnly: true });
  const serviceChoices = itemSearch(orgId, { type: "service" });
  // A service already on the list is not offered again.
  const services: PickerSearch = async (query, signal) => {
    const result = await serviceChoices(query, signal);
    return result.ok ? { ...result, data: result.data.filter((entity) => !state.services.some((chosen) => chosen.id === entity.id)) } : result;
  };
  const horseChoices = horseSearch(orgId);
  // A horse already on the list is not offered again.
  const horses: PickerSearch = async (query, signal) => {
    const result = await horseChoices(query, signal);
    return result.ok ? { ...result, data: result.data.filter((entity) => !state.horses.some((chosen) => chosen.id === entity.id)) } : result;
  };
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

  const subjects = () => state.horses.map((horse) => ({ subject_type: SUBJECT_TYPE, subject_id: horse.id }));

  async function create() {
    const when = instants();
    if (when === null) return;
    if (state.practitioner === NOBODY) {
      setLocal(["Choose who does the work."]);
      return;
    }
    const body: AppointmentCreate = {
      title: state.title,
      ...when,
      location: blankToNull(state.location),
      notes: blankToNull(state.notes),
      practitioner_user_id: state.practitioner,
      customer_id: state.customer?.id ?? null,
      subjects: subjects(),
      items: state.services.map((service) => service.id),
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
    if (state.practitioner !== NOBODY && state.practitioner !== current.practitioner_user_id) body.practitioner_user_id = state.practitioner; // never cleared
    if ((state.customer?.id ?? null) !== current.customer_id) body.customer_id = state.customer?.id ?? null;
    const had = current.subjects.filter((subject) => subject.subject_type === SUBJECT_TYPE).map((subject) => subject.subject_id).sort();
    if (JSON.stringify(had) !== JSON.stringify(state.horses.map((horse) => horse.id).sort())) body.subjects = subjects();
    const hadServices = current.items.map((item) => item.item_id).sort();
    if (JSON.stringify(hadServices) !== JSON.stringify(state.services.map((service) => service.id).sort())) body.items = state.services.map((service) => service.id);
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

  const dirty = record !== undefined && JSON.stringify(state) !== JSON.stringify(toState(zone, prefill, record));

  async function makeOrder(current: Appointment) {
    setOrderNotice(null);
    const made = await run(() => apiFetch<Appointment>(orgId, `/appointments/${current.id}/order`, { method: "POST", ifMatch: current.version }));
    if (made === null) return;
    setRecord(made);
    setState(toState(zone, prefill, made));
    setOrderNotice(`Draft order ${made.order_number ?? ""} made.`.replace("  ", " "));
    router.refresh();
  }

  async function updateOrder(current: Appointment) {
    setOrderNotice(null);
    const done = await run(() => apiFetch<OrderUpdateResult>(orgId, `/appointments/${current.id}/order/update`, { method: "POST", ifMatch: current.version }));
    if (done === null) return;
    setRecord(done.appointment);
    const parts = [`${done.added} ${done.added === 1 ? "line" : "lines"} added`, `${done.removed} removed`];
    if (done.kept > 0) parts.push(`${done.kept} kept because it was changed on the order`);
    setOrderNotice(`Order updated: ${parts.join(", ")}.`);
    router.refresh();
  }

  async function cancelOrder(current: Appointment) {
    setOrderNotice(null);
    const done = await run(() => apiFetch<Appointment>(orgId, `/appointments/${current.id}/order/cancel`, { method: "POST", ifMatch: current.version }));
    if (done === null) return;
    setRecord(done);
    setOrderNotice(`Order ${done.order_number ?? ""} cancelled.`.replace("  ", " "));
    router.refresh();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    void (record ? save(record) : create());
  }

  const cancelled = record?.status === "cancelled";
  const hasDraftOrder = record !== undefined && record.order_id !== null && record.order_number !== null && record.order_status === "draft";
  const personOptions = [...(state.practitioner === NOBODY ? [{ value: NOBODY, label: "Choose a practitioner" }] : []), ...people.map((person) => ({ value: person.user_id, label: person.name }))];
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
        <SelectField label="Practitioner" name="practitioner_user_id" value={state.practitioner} onChange={(value) => set("practitioner", value)} options={personOptions} error={problems.byField.practitioner_user_id} hint="Who does the work: always a person." />
        <EntityPicker label="Customer" name="customer_id" value={state.customer} onChange={(customer) => set("customer", customer)} search={customers} clearable error={problems.byField.customer_id} hint="Optional." />
        <div className="flex flex-col gap-2" data-testid="horses">
          <EntityPicker
            label="Horses"
            name="subjects"
            value={null}
            onChange={(horse) => horse && set("horses", [...state.horses, horse])}
            search={horses}
            error={problems.byField.subjects}
            hint={state.horses.length === 0 ? "Optional. Pick one horse after another to book a stable's horses in one session." : `${state.horses.length} chosen. Pick another to add it.`}
          />
          {state.horses.length > 0 && (
            <ul className="flex flex-wrap gap-2" aria-label="Chosen horses">
              {state.horses.map((horse) => (
                <li key={horse.id} className="badge tone-primary !px-2.5 !py-1 !text-sm gap-1.5" data-testid="chosen-horse">
                  {horse.label}
                  <button type="button" aria-label={`Remove ${horse.label}`} className="font-bold" onClick={() => set("horses", state.horses.filter((chosen) => chosen.id !== horse.id))}>
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex flex-col gap-2" data-testid="services">
          <EntityPicker
            label="Services"
            name="items"
            value={null}
            onChange={(service) => service && set("services", [...state.services, service])}
            search={services}
            error={problems.byField.items}
            hint={state.services.length === 0 ? "Optional. What is done at this appointment; it is what the order is made from." : `${state.services.length} chosen. Pick another to add it.`}
          />
          {state.services.length > 0 && (
            <ul className="flex flex-wrap gap-2" aria-label="Chosen services">
              {state.services.map((service) => (
                <li key={service.id} className="badge tone-success !px-2.5 !py-1 !text-sm gap-1.5" data-testid="chosen-service">
                  {service.label}
                  <button type="button" aria-label={`Remove ${service.label}`} className="font-bold" onClick={() => set("services", state.services.filter((chosen) => chosen.id !== service.id))}>
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
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
        <section aria-label="Order" className="panel flex max-w-3xl flex-col gap-2" data-testid="appointment-order">
          <h2 className="text-lg font-semibold">Order</h2>
          {record.order_id !== null && record.order_number !== null ? (
            <>
              <p className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-zinc-500">This appointment is billed on:</span>
                <Link href={`/o/${orgId}/transactions/${record.order_id}`} className="badge tone-primary !px-3 !py-1 !text-sm after:ml-1.5 after:content-['→']" data-testid="appointment-order-link">
                  Order {record.order_number}
                </Link>
                <span className="text-zinc-500" data-testid="appointment-order-status">
                  {record.order_status === "draft" ? "draft" : record.order_status}
                </span>
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Button type="button" disabled={pending || dirty || record.order_status !== "draft"} onClick={() => void updateOrder(record)} data-testid="update-order">
                  Update order
                </Button>
                <ConfirmButton
                  label="Cancel order"
                  question={`Cancel draft order ${record.order_number}? It stays on record.`}
                  confirmLabel="Yes, cancel the order"
                  declineLabel="No, keep the order"
                  disabled={pending || record.order_status !== "draft"}
                  onConfirm={() => void cancelOrder(record)}
                  testId="cancel-order"
                />
                <span className="text-xs text-zinc-500">
                  {record.order_status !== "draft"
                    ? "The order is no longer a draft, so it is changed on the order itself."
                    : dirty
                      ? "Save the appointment first: the order is made from what is saved."
                      : "Adds lines for horses and services added since, and removes the ones taken away. Lines you changed or added on the order stay."}
                </span>
              </div>
            </>
          ) : (
            <>
              {record.order_id !== null && <p className="text-sm text-zinc-500" data-testid="order-gone">The order made from this appointment no longer exists.</p>}
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  type="button"
                  disabled={pending || dirty || cancelled || record.customer_id === null || record.items.length === 0}
                  onClick={() => void makeOrder(record)}
                  data-testid="create-order"
                >
                  Create draft order
                </Button>
                <span className="text-xs text-zinc-500">
                  {cancelled
                    ? "A cancelled appointment cannot make an order."
                    : record.customer_id === null
                      ? "Choose a customer and save: the order is billed to them."
                      : record.items.length === 0
                        ? "Choose at least one service and save: the order has a line for each service and each horse."
                        : dirty
                          ? "Save the appointment first: the order is made from what is saved."
                          : "Makes a draft order with a line for each service and each horse."}
                </span>
              </div>
            </>
          )}
          {orderNotice && (
            <p role="status" className="text-sm" data-testid="order-notice">
              {orderNotice}
            </p>
          )}
        </section>
      )}
      {record && (
        <div className="flex flex-wrap items-center gap-3" data-testid="appointment-actions">
          {cancelled ? (
            <Button type="button" disabled={pending} onClick={() => void change(record, { status: "scheduled" })} data-testid="restore">
              Restore this appointment
            </Button>
          ) : (
            <ConfirmButton
              label="Cancel appointment"
              question={hasDraftOrder ? `Cancel this appointment? It stays on record. Its draft order ${record.order_number} can be cancelled too.` : "Cancel this appointment? It stays on record."}
              confirmLabel={hasDraftOrder ? "Cancel appointment" : "Yes, cancel it"}
              alsoLabel={hasDraftOrder ? "Cancel appointment and order" : undefined}
              onAlso={hasDraftOrder ? () => void change(record, { status: "cancelled", cancel_order: true }) : undefined}
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
