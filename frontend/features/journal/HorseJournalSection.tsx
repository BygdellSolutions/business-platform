import Link from "next/link";

import { PRIMARY_LINK } from "@/components/ui/Button";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import type { JournalSummary } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/timestamps";
import { NewJournalEntry } from "./NewJournalEntry";

/** How many of the latest entries the horse's page lists; the journal page has all of them. */
export const SHOWN_ON_HORSE_PAGE = 10;

/**
 * The journal on the horse's page, a section like "Services performed on this horse": closed until its heading is opened, with a
 * count, the latest entries (newest visit first), a button to start a new entry and a link to the whole journal.
 */
export function HorseJournalSection({
  orgId,
  horseId,
  entries,
  hasMore,
  timeZone,
}: {
  orgId: string;
  horseId: string;
  entries: JournalSummary[];
  hasMore: boolean;
  timeZone: string | null;
}) {
  const base = `/o/${orgId}/horses/${horseId}/journal`;
  return (
    <CollapsibleSection title="Journal of visits and treatments" count={entries.length} testId="journal-section" toggleTestId="journal-toggle">
      {/* The two things to do here, as the same kind of button: start a new entry, or open the whole journal. */}
      <NewJournalEntry horseId={horseId}>
        <Link href={base} className={PRIMARY_LINK} data-testid="journal-all">
          {hasMore ? "Open the whole journal" : "Open the journal"}
        </Link>
      </NewJournalEntry>
      {entries.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-journal-entries">
          No journal entries yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="text-left text-sm" data-testid="journal-table">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Visit</th>
                <th className="py-1 pr-4">By</th>
                <th className="py-1 pr-4">Status</th>
                <th className="py-1 pr-4">Marks</th>
                <th className="py-1">Notes</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id} data-testid="journal-row" className="border-b border-zinc-200 align-top dark:border-zinc-800">
                  <td className="py-1 pr-4 whitespace-nowrap">
                    <Link href={`${base}/${entry.id}`} className="underline">
                      {formatTimestamp(entry.visit_at, timeZone)}
                    </Link>
                  </td>
                  <td className="py-1 pr-4">{entry.practitioner_name}</td>
                  <td className="py-1 pr-4" data-testid="entry-status">
                    {entry.status === "draft" ? "Draft" : "Finalized"}
                  </td>
                  <td className="py-1 pr-4 text-right">{entry.mark_count}</td>
                  <td className="py-1 text-zinc-700 dark:text-zinc-300">{entry.preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {hasMore && <p className="text-sm text-zinc-500">Showing the latest {entries.length} entries; the journal has more.</p>}
    </CollapsibleSection>
  );
}
