import type { HistoryChange, HistoryEvent } from "@/lib/api/types";
import { ENTITY_LABELS, FIELD_LABELS, REFERENCE_FIELDS, actionLabel } from "@/lib/history-labels";
import type { RecordHistoryData } from "@/lib/history-server";
import { formatTimestamp } from "@/lib/timestamps";

function shown(field: string, value: HistoryChange["from"], names: Record<string, string>): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (REFERENCE_FIELDS[field] && typeof value === "string") return names[value] ?? "a record that no longer exists";
  return String(value);
}

function Change({ entityType, field, change, names }: { entityType: string; field: string; change: HistoryChange; names: Record<string, string> }) {
  const label = change.label ?? FIELD_LABELS[entityType]?.[field] ?? field;
  return (
    <li data-testid="history-change">
      <span className="font-medium">{label}</span>: <span className="text-zinc-500">{shown(field, change.from, names)}</span> → {shown(field, change.to, names)}
    </li>
  );
}

function Entry({ event, ownType, timeZone, names }: { event: HistoryEvent; ownType: string; timeZone: string | null; names: Record<string, string> }) {
  const about = event.entity_type === ownType ? "" : `${ENTITY_LABELS[event.entity_type] ?? event.entity_type} · `;
  // A creation or deletion lists every value; a change lists only what differs.
  const changes = Object.entries(event.changes);
  return (
    <li data-testid="history-event" data-action={event.action} className="border-b border-zinc-200 py-2 dark:border-zinc-800">
      <div className="text-sm">
        <span className="font-medium">
          {about}
          {actionLabel(event.action)}
        </span>{" "}
        <span className="text-zinc-500">
          {formatTimestamp(event.occurred_at, timeZone)} · {event.actor?.name ?? "unknown user"}
        </span>
      </div>
      {changes.length > 0 && event.action !== "deleted" && (
        <ul className="mt-1 list-disc pl-5 text-sm">
          {changes.map(([field, change]) => (
            <Change key={field} entityType={event.entity_type} field={field} change={change} names={names} />
          ))}
        </ul>
      )}
    </li>
  );
}

/** The history of one record, newest first: who did what, when, and what each value was before. */
export function RecordHistory({ data, entityType, timeZone }: { data: RecordHistoryData; entityType: string; timeZone: string | null }) {
  return (
    <section aria-label="History" data-testid="history" className="flex max-w-3xl flex-col gap-1">
      <h2 className="text-lg font-semibold">History</h2>
      {data.history.events.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="history-empty">
          No changes recorded yet. (Changes made before history was kept are not listed.)
        </p>
      ) : (
        <ul>
          {data.history.events.map((event) => (
            <Entry key={event.id} event={event} ownType={entityType} timeZone={timeZone} names={data.names} />
          ))}
        </ul>
      )}
    </section>
  );
}
