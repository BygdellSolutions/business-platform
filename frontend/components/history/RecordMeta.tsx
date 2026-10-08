import type { Person } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/timestamps";

interface Authored {
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

/** "Created … by …, last changed … by …". An author that was never recorded says so instead of guessing. */
export function RecordMeta({ record, people, timeZone }: { record: Authored; people: Person[]; timeZone: string | null }) {
  const name = (id: string | null) => (id === null ? "not recorded" : (people.find((person) => person.id === id)?.name ?? "unknown user"));
  return (
    <p className="text-sm text-zinc-500" data-testid="record-meta">
      Created {formatTimestamp(record.created_at, timeZone)} by <span data-testid="created-by">{name(record.created_by)}</span> · Last changed{" "}
      {formatTimestamp(record.updated_at, timeZone)} by <span data-testid="updated-by">{name(record.updated_by)}</span>
    </p>
  );
}
