import { Calendar } from "@/features/calendar/Calendar";
import { readActiveRole } from "@/lib/active-role";
import type { Appointment, Colleague, Organization } from "@/lib/api/types";
import { VIEWS, isRealDay, windowOf, type CalendarView } from "@/lib/calendar";
import { canWriteRecords } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";
import { formatDay } from "@/lib/timestamps";
import { isUuid } from "@/lib/uuid";

const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

/** The calendar: one day (a column per person), a week or a month, for everyone or one person. Everything is read on the server, so the address says what is shown. */
export default async function CalendarPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const raw = await searchParams;
  const [role, organization, people] = await Promise.all([
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    serverRead<Colleague[]>(orgId, "/api/members/people", "?journal=true"),
  ]);
  const zone = organization.timezone;
  const today = formatDay(new Date().toISOString(), zone);
  const view = (VIEWS as readonly string[]).includes(first(raw.view) ?? "") ? (first(raw.view) as CalendarView) : "week";
  const requested = first(raw.date) ?? "";
  const day = isRealDay(requested) ? requested : today;
  // A person who is not among the people (another organization's id, a random one) simply narrows to nobody: nothing is revealed.
  const who = first(raw.who) && isUuid(first(raw.who) ?? "") ? (first(raw.who) as string) : null;

  const { from, to } = windowOf(view, day, zone);
  const query = new URLSearchParams({ from, to });
  if (who) query.set("practitioner_user_id", who);
  const appointments = await serverRead<Appointment[]>(orgId, "/api/appointments", `?${query}`);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Scheduling</h1>
      <Calendar orgId={orgId} view={view} day={day} today={today} who={who} people={people} appointments={appointments} zone={zone} canWrite={canWriteRecords(role)} />
    </div>
  );
}
