import Link from "next/link";

import { SECONDARY_LINK } from "@/components/ui/Button";

import { localParts, timeText } from "@/lib/calendar";
import type { Appointment } from "@/lib/api/types";

/** Today's bookings in time order, each linking to the booking; cancelled ones are left out. */
export function TodayList({ orgId, appointments, zone }: { orgId: string; appointments: Appointment[]; zone: string | null }) {
  const live = appointments.filter((appointment) => appointment.status === "scheduled");
  return (
    <section aria-label="Today" className="flex flex-col gap-2" data-testid="today">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Today</h2>
        <Link href={`/o/${orgId}/calendar`} className={SECONDARY_LINK}>
          Open scheduling
        </Link>
      </div>
      {live.length === 0 ? (
        <p className="panel text-sm text-secondary" data-testid="today-empty">
          Nothing is booked today.
        </p>
      ) : (
        <ul className="panel flex flex-col divide-y divide-border p-0">
          {live.map((appointment) => (
            <li key={appointment.id}>
              <Link href={`/o/${orgId}/calendar/${appointment.id}`} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-2.5 hover:bg-surface-hover" data-testid="today-row">
                <span className="w-24 shrink-0 font-medium tabular-nums">
                  {timeText(localParts(appointment.starts_at, zone).minutes)}–{timeText(localParts(appointment.ends_at, zone).minutes)}
                </span>
                <span className="font-medium">{appointment.title}</span>
                <span className="text-sm text-secondary">
                  {[appointment.customer_name, appointment.subjects.map((subject) => subject.label).filter(Boolean).join(", "), appointment.practitioner_name]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
