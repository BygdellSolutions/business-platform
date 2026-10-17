import Link from "next/link";

import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { SECONDARY_LINK } from "@/components/ui/Button";
import { AppointmentForm } from "@/features/calendar/AppointmentForm";
import { readActiveRole } from "@/lib/active-role";
import type { Appointment, Colleague, Organization } from "@/lib/api/types";
import { localParts } from "@/lib/calendar";
import { readRecordHistory } from "@/lib/history-server";
import { canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";
import { formatDay } from "@/lib/timestamps";

/** Another organization's appointment, a random id and a malformed id all end in the same generic not-found page. */
export default async function AppointmentPage({ params }: { params: Promise<{ orgId: string; id: string }> }) {
  const { orgId, id } = await params;
  const recordId = requireUuid(id);
  const [appointment, role, organization, people, history] = await Promise.all([
    serverRead<Appointment>(orgId, `/api/appointments/${recordId}`),
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    serverRead<Colleague[]>(orgId, "/api/members/people", "?journal=true"),
    readRecordHistory(orgId, "appointment", recordId),
  ]);
  const zone = organization.timezone;
  const day = localParts(appointment.starts_at, zone).day;
  const back = `/o/${orgId}/calendar?${new URLSearchParams({ view: "week", date: day })}`;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight" data-testid="record-name">
          {appointment.title}
        </h1>
        <Link href={back} className={SECONDARY_LINK}>
          Back to the calendar
        </Link>
      </div>
      <p className="text-sm text-zinc-500" data-testid="record-number">
        Appointment no. {appointment.number}
      </p>
      <RecordMeta record={appointment} people={history.history.people} timeZone={zone} />
      {canWriteRecords(role) ? (
        <AppointmentForm key={`${appointment.id}-${appointment.version}`} appointment={appointment} zone={zone} people={people} today={formatDay(new Date().toISOString(), zone)} prefill={{ day, start: null, who: null }} />
      ) : (
        <AppointmentSummary appointment={appointment} zone={zone} />
      )}
      <RecordHistory data={history} entityType="appointment" timeZone={zone} />
    </div>
  );
}

function AppointmentSummary({ appointment, zone }: { appointment: Appointment; zone: string | null }) {
  const start = localParts(appointment.starts_at, zone);
  const end = localParts(appointment.ends_at, zone);
  const time = (parts: { day: string; minutes: number }) => `${parts.day} ${String(Math.floor(parts.minutes / 60)).padStart(2, "0")}:${String(parts.minutes % 60).padStart(2, "0")}`;
  const rows: [string, string | null][] = [
    ["When", `${time(start)} – ${end.day === start.day ? time(end).slice(11) : time(end)}`],
    ["Status", appointment.status === "cancelled" ? "Cancelled" : "Scheduled"],
    ["Practitioner", appointment.practitioner_name],
    ["Customer", appointment.customer_name],
    ["Horses", appointment.subjects.map((subject) => subject.label ?? "a horse").join(", ") || null],
    ["Services", appointment.items.map((item) => item.name ?? "a service").join(", ") || null],
    ["Order", appointment.order_number !== null ? `Order ${appointment.order_number} (${appointment.order_status})` : null],
    ["Location", appointment.location],
    ["Notes", appointment.notes],
  ];
  return (
    <dl className="panel grid max-w-xl grid-cols-[8rem_1fr] gap-2 text-sm" data-testid="appointment-summary">
      {rows.filter(([, value]) => value).map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-secondary">{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
