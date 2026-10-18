import Link from "next/link";

import { BUTTON_VARIANTS, PRIMARY_LINK } from "@/components/ui/Button";
import { CalendarDnD } from "@/features/calendar/CalendarDnD";
import type { Appointment, Colleague } from "@/lib/api/types";
import { addDays } from "@/lib/dates";
import {
  VIEWS,
  clashing,
  dayOfMonth,
  daysOf,
  hourRange,
  localParts,
  place,
  step,
  timeText,
  titleOf,
  weekdayName,
  type CalendarView,
} from "@/lib/calendar";

/** One tint per person, in the order of the people list (the same person has the same colour in every view). */
const TONES = ["tone-primary", "tone-success", "tone-warning", "tone-purple", "tone-info", "tone-orange", "tone-danger", "tone-neutral"] as const;
const UNASSIGNED_TONE = "tone-neutral";
const HOUR_PX = 48;
const SLOT_MINUTES = 30;

export interface CalendarProps {
  orgId: string;
  view: CalendarView;
  day: string;
  today: string;
  /** The person the calendar is narrowed to, or null for everyone. */
  who: string | null;
  people: Colleague[];
  appointments: Appointment[];
  zone: string | null;
  canWrite: boolean;
}

export function calendarHref(orgId: string, view: CalendarView, day: string, who: string | null): string {
  const params = new URLSearchParams({ view, date: day });
  if (who) params.set("who", who);
  return `/o/${orgId}/calendar?${params}`;
}

function newHref(orgId: string, day: string, start: string | null, who: string | null): string {
  const params = new URLSearchParams({ date: day });
  if (start) params.set("start", start);
  if (who) params.set("who", who);
  return `/o/${orgId}/calendar/new?${params}`;
}

function toneOf(props: Pick<CalendarProps, "people">, userId: string | null): string {
  const index = userId === null ? -1 : props.people.findIndex((person) => person.user_id === userId);
  return index < 0 ? UNASSIGNED_TONE : TONES[index % TONES.length];
}

/** What the drag layer needs to know about a booking it may move (see CalendarDnD). */
function dragData(appointment: Appointment) {
  return {
    "data-movable": "true",
    "data-appointment-id": appointment.id,
    "data-version": appointment.version,
    "data-start": appointment.starts_at,
    "data-end": appointment.ends_at,
    "data-who": appointment.practitioner_user_id ?? "",
  };
}

/** The names of the horses an appointment is about, "Kalle, Storm" (empty when none). */
export function horsesOf(appointment: Appointment): string {
  return appointment.subjects.map((subject) => subject.label ?? "a horse").join(", ");
}

const NAV_BUTTON = `${BUTTON_VARIANTS.secondary}`;

export function Calendar(props: CalendarProps) {
  const { orgId, view, day, who, people, appointments, canWrite } = props;
  const link = (nextView: CalendarView, nextDay: string, nextWho: string | null = who) => calendarHref(orgId, nextView, nextDay, nextWho);
  const clashes = clashing(appointments);

  return (
    <div className="flex flex-col gap-4" data-testid="calendar" data-view={view}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={link(view, props.today)} className={NAV_BUTTON} data-testid="calendar-today">
            Today
          </Link>
          <Link href={link(view, step(view, day, -1))} className={NAV_BUTTON} aria-label="Previous" data-testid="calendar-previous">
            ‹
          </Link>
          <Link href={link(view, step(view, day, 1))} className={NAV_BUTTON} aria-label="Next" data-testid="calendar-next">
            ›
          </Link>
          <h2 className="px-2 text-lg font-semibold" data-testid="calendar-title">
            {titleOf(view, day)}
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="View" className="flex gap-1">
            {VIEWS.map((option) => (
              <Link
                key={option}
                href={link(option, day)}
                aria-current={option === view ? "page" : undefined}
                className={option === view ? BUTTON_VARIANTS.primary : BUTTON_VARIANTS.secondary}
                data-testid={`view-${option}`}
              >
                {option[0].toUpperCase() + option.slice(1)}
              </Link>
            ))}
          </div>
          {canWrite && (
            <Link href={newHref(orgId, view === "month" ? props.today : day, null, who)} className={PRIMARY_LINK} data-testid="new-appointment">
              New appointment
            </Link>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="calendar-people">
        <span className="text-secondary">Show:</span>
        <Link href={link(view, day, null)} aria-current={who === null ? "true" : undefined} className={who === null ? BUTTON_VARIANTS.primary : BUTTON_VARIANTS.secondary}>
          Everyone
        </Link>
        {people.map((person) => (
          <Link
            key={person.user_id}
            href={link(view, day, person.user_id)}
            aria-current={who === person.user_id ? "true" : undefined}
            className={who === person.user_id ? BUTTON_VARIANTS.primary : BUTTON_VARIANTS.secondary}
            data-testid="person-filter"
          >
            <span aria-hidden className={`${toneOf(props, person.user_id)} inline-block h-2.5 w-2.5 rounded-full`} style={{ backgroundColor: "var(--tone)" }} />
            {person.name}
          </Link>
        ))}
      </div>

      {view === "month" ? (
        <MonthGrid {...props} link={link} clashes={clashes} />
      ) : view === "week" ? (
        <TimeGrid
          {...props}
          clashes={clashes}
          assign={false}
          columns={daysOf("week", day).map((columnDay) => ({
            key: columnDay,
            heading: (
              <Link href={link("day", columnDay)} className={columnDay === props.today ? "font-semibold text-accent" : ""}>
                {weekdayName(columnDay)} {dayOfMonth(columnDay)}
              </Link>
            ),
            day: columnDay,
            who,
            items: appointments,
          }))}
          showPerson
        />
      ) : (
        <TimeGrid
          {...props}
          clashes={clashes}
          assign
          columns={[
            ...people
              .filter((person) => who === null || person.user_id === who)
              .map((person) => ({
                key: person.user_id,
                heading: <span className="font-medium">{person.name}</span>,
                day,
                who: person.user_id,
                items: appointments.filter((a) => a.practitioner_user_id === person.user_id),
              })),
            ...(who === null && appointments.some((a) => a.practitioner_user_id === null)
              ? [{ key: "none", heading: <span className="text-secondary">Nobody in particular</span>, day, who: null, items: appointments.filter((a) => a.practitioner_user_id === null) }]
              : []),
          ]}
          showPerson={false}
        />
      )}
    </div>
  );
}

interface Column {
  key: string;
  heading: React.ReactNode;
  day: string;
  who: string | null;
  items: Appointment[];
}

function TimeGrid(props: CalendarProps & { columns: Column[]; clashes: Set<string>; showPerson: boolean; assign: boolean }) {
  const { orgId, zone, columns, canWrite, clashes, showPerson, assign } = props;
  const placed = columns.map((column) => ({ column, blocks: place(column.items, column.day, zone) }));
  const { first, last } = hourRange(placed.flatMap((entry) => entry.blocks));
  const hours = Array.from({ length: last - first }, (_, index) => first + index);
  const slots = Array.from({ length: ((last - first) * 60) / SLOT_MINUTES }, (_, index) => first * 60 + index * SLOT_MINUTES);
  const px = (minutes: number) => ((minutes - first * 60) / 60) * HOUR_PX;
  // The line for "now" is drawn where the page was made (the server's clock), in today's column only, and only inside the hours shown.
  const now = localParts(new Date().toISOString(), zone);
  const nowShown = now.minutes >= first * 60 && now.minutes < last * 60;

  if (columns.length === 0) return <p data-testid="calendar-empty">There is nobody to show. Add colleagues under Members.</p>;

  return (
    <CalendarDnD orgId={orgId} zone={zone} mode="time" firstHour={first} hourPx={HOUR_PX} enabled={canWrite}>
    <div className="overflow-x-auto rounded-lg border border-border-strong">
      <div className="grid min-w-[44rem]" style={{ gridTemplateColumns: `3.5rem repeat(${columns.length}, minmax(0, 1fr))` }}>
        <div className="border-b border-border-strong" />
        {placed.map(({ column }) => (
          <div key={column.key} className="border-b border-l border-border-strong px-2 py-1.5 text-center text-sm" data-testid="column-heading">
            {column.heading}
          </div>
        ))}
        <div className="relative" style={{ height: hours.length * HOUR_PX }} aria-hidden>
          {hours.map((hour) => (
            <div key={hour} className="absolute right-1.5 -translate-y-1/2 text-xs text-secondary" style={{ top: (hour - first) * HOUR_PX + (hour === first ? 8 : 0) }}>
              {timeText(hour * 60)}
            </div>
          ))}
        </div>
        {placed.map(({ column, blocks }) => (
          <div key={column.key} className="relative border-l border-border-strong" style={{ height: hours.length * HOUR_PX }} data-testid="column" data-column data-day={column.day} data-who={column.who ?? ""} data-assign={assign ? "true" : undefined}>
            {hours.map((hour) => (
              <div key={hour} className="absolute inset-x-0 border-t border-border" style={{ top: (hour - first) * HOUR_PX }} aria-hidden />
            ))}
            {canWrite &&
              slots.map((minutes) => (
                <Link
                  key={minutes}
                  href={newHref(orgId, column.day, timeText(minutes), column.who)}
                  tabIndex={-1}
                  aria-label={`New appointment ${column.day} ${timeText(minutes)}`}
                  className="absolute inset-x-0 hover:bg-surface-hover"
                  style={{ top: px(minutes), height: (SLOT_MINUTES / 60) * HOUR_PX }}
                  data-testid="slot"
                />
              ))}
            {nowShown && column.day === now.day && (
              <div className="pointer-events-none absolute inset-x-0 z-10 border-t-2 border-red-600" style={{ top: px(now.minutes) }} data-testid="now-line" aria-hidden>
                <span className="absolute -top-[5px] -left-1 size-2 rounded-full bg-red-600" />
              </div>
            )}
            {blocks.map(({ item, top, height, lane, lanes }) => (
              <Block key={`${item.id}-${column.day}`} {...props} day={column.day} appointment={item} top={px(top)} height={Math.max((height / 60) * HOUR_PX, 22)} lane={lane} lanes={lanes} clash={clashes.has(item.id)} showPerson={showPerson} />
            ))}
          </div>
        ))}
      </div>
    </div>
    </CalendarDnD>
  );
}

function Block({
  orgId,
  zone,
  people,
  canWrite,
  day,
  appointment,
  top,
  height,
  lane,
  lanes,
  clash,
  showPerson,
}: CalendarProps & { day: string; appointment: Appointment; top: number; height: number; lane: number; lanes: number; clash: boolean; showPerson: boolean }) {
  const start = localParts(appointment.starts_at, zone);
  const end = localParts(appointment.ends_at, zone);
  const range = `${timeText(start.minutes)}–${timeText(end.minutes)}`;
  const who = showPerson && appointment.practitioner_name ? ` · ${appointment.practitioner_name}` : "";
  const horses = horsesOf(appointment);
  // A booking can be moved from the day it starts on, and its end dragged when it also ends on that day (a clipped one is changed in the form).
  const movable = canWrite && start.day === day;
  const resizable = movable && end.day === day;
  return (
    <Link
      href={`/o/${orgId}/calendar/${appointment.id}`}
      className={`${toneOf({ people }, appointment.practitioner_user_id)} tint absolute overflow-hidden rounded px-1.5 py-0.5 text-xs leading-tight hover:brightness-95 ${clash ? "ring-2 ring-red-600" : ""}`}
      style={{ top, height, left: `calc(${(lane / lanes) * 100}% + 2px)`, width: `calc(${100 / lanes}% - 4px)`, color: "var(--tone-text)", touchAction: movable ? "none" : undefined }}
      title={clash ? "Overlaps another booking of the same person" : undefined}
      draggable={false}
      data-testid="appointment"
      data-clash={clash ? "true" : undefined}
      {...(movable ? dragData(appointment) : {})}
    >
      <span className="font-semibold">{clash ? "⚠ " : ""}{appointment.title}</span>
      <br />
      <span>
        {range}
        {who}
      </span>
      {horses && height > 34 && (
        <>
          <br />
          <span data-testid="appointment-horses">{horses}</span>
        </>
      )}
      {appointment.customer_name && height > 56 && (
        <>
          <br />
          <span>{appointment.customer_name}</span>
        </>
      )}
      {appointment.order_number !== null && height > 68 && (
        <>
          <br />
          <span data-testid="appointment-order-mark">Order {appointment.order_number}</span>
        </>
      )}
      {resizable && <span data-resize aria-hidden className="absolute inset-x-0 bottom-0 h-1.5 cursor-ns-resize" />}
    </Link>
  );
}

function MonthGrid(props: CalendarProps & { link: (view: CalendarView, day: string) => string; clashes: Set<string> }) {
  const { orgId, day, today, zone, appointments, people, link, clashes } = props;
  const days = daysOf("month", day);
  const byDay = new Map<string, Appointment[]>();
  for (const appointment of appointments) {
    // A booking that spans days appears on each day it touches.
    let cursor = localParts(appointment.starts_at, zone).day;
    const lastDay = localParts(new Date(new Date(appointment.ends_at).getTime() - 1).toISOString(), zone).day;
    for (let guard = 0; cursor <= lastDay && guard < 8; guard++) {
      byDay.set(cursor, [...(byDay.get(cursor) ?? []), appointment]);
      cursor = addDays(cursor, 1);
    }
  }
  return (
    <CalendarDnD orgId={orgId} zone={zone} mode="month" enabled={props.canWrite}>
    <div className="overflow-x-auto rounded-lg border border-border-strong">
      <div className="grid min-w-[44rem] grid-cols-7">
        {days.slice(0, 7).map((name) => (
          <div key={name} className="border-b border-border-strong px-2 py-1 text-center text-xs font-medium text-secondary">
            {weekdayName(name)}
          </div>
        ))}
        {days.map((cell) => {
          const items = byDay.get(cell) ?? [];
          const inMonth = cell.slice(0, 7) === day.slice(0, 7);
          return (
            <div key={cell} className={`min-h-24 border-t border-l border-border-strong p-1 ${inMonth ? "" : "bg-surface-hover/40 text-secondary"}`} data-testid="month-day" data-month-day data-day={cell}>
              <Link href={link("day", cell)} className={`text-xs ${cell === today ? "rounded bg-accent px-1.5 py-0.5 font-semibold text-white" : "font-medium"}`}>
                {dayOfMonth(cell)}
              </Link>
              <ul className="mt-1 flex flex-col gap-0.5">
                {items.slice(0, 3).map((appointment) => (
                  <li key={appointment.id}>
                    <Link
                      href={`/o/${orgId}/calendar/${appointment.id}`}
                      className={`${toneOf({ people }, appointment.practitioner_user_id)} tint relative block truncate rounded px-1 text-xs ${clashes.has(appointment.id) ? "ring-1 ring-red-600" : ""}`}
                      style={{ color: "var(--tone-text)", touchAction: props.canWrite ? "none" : undefined }}
                      title={[appointment.title, horsesOf(appointment)].filter(Boolean).join(" · ")}
                      draggable={false}
                      data-testid="appointment"
                      {...(props.canWrite ? dragData(appointment) : {})}
                    >
                      {timeText(localParts(appointment.starts_at, zone).minutes)} {appointment.title}
                      {horsesOf(appointment) && <span className="opacity-75"> · {horsesOf(appointment)}</span>}
                    </Link>
                  </li>
                ))}
                {items.length > 3 && (
                  <li>
                    <Link href={link("day", cell)} className="text-xs text-secondary underline">
                      +{items.length - 3} more
                    </Link>
                  </li>
                )}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
    </CalendarDnD>
  );
}
