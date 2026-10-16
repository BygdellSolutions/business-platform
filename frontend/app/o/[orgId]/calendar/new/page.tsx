import { NotAllowed } from "@/components/ui/NotAllowed";
import { AppointmentForm } from "@/features/calendar/AppointmentForm";
import { readActiveRole } from "@/lib/active-role";
import type { Colleague, Organization } from "@/lib/api/types";
import { isRealDay, minutesOf } from "@/lib/calendar";
import { canWriteRecords } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";
import { formatDay } from "@/lib/timestamps";
import { isUuid } from "@/lib/uuid";

const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

export default async function NewAppointmentPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  if (!canWriteRecords(await readActiveRole(orgId))) {
    return <NotAllowed what="appointments" back={`/o/${orgId}/calendar`} backLabel="Back to the calendar" />;
  }
  const raw = await searchParams;
  const [organization, people] = await Promise.all([serverRead<Organization>(orgId, "/api/organization"), serverRead<Colleague[]>(orgId, "/api/members/people", "?journal=true")]);
  const today = formatDay(new Date().toISOString(), organization.timezone);
  const date = first(raw.date) ?? "";
  const start = first(raw.start) ?? "";
  const who = first(raw.who) ?? "";
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-[28px] leading-tight font-semibold tracking-tight">New appointment</h1>
      <AppointmentForm
        zone={organization.timezone}
        people={people}
        today={today}
        prefill={{ day: isRealDay(date) ? date : today, start: minutesOf(start) === null ? null : start, who: isUuid(who) && people.some((person) => person.user_id === who) ? who : null }}
      />
    </div>
  );
}
