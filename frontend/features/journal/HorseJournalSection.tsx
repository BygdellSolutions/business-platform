import Link from "next/link";

import { PRIMARY_LINK } from "@/components/ui/Button";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import type { JournalSummary } from "@/lib/api/types";
import { JournalTable } from "./JournalTable";
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
  bare = false,
}: {
  orgId: string;
  horseId: string;
  entries: JournalSummary[];
  hasMore: boolean;
  timeZone: string | null;
  /** Shown inside a pop-up window, which has the title and the frame already. */
  bare?: boolean;
}) {
  const base = `/o/${orgId}/horses/${horseId}/journal`;
  const body = (
    <>
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
        <JournalTable orgId={orgId} horseId={horseId} entries={entries} timeZone={timeZone} />
      )}
      {hasMore && <p className="text-sm text-zinc-500">Showing the latest {entries.length} entries; the journal has more.</p>}
    </>
  );
  // In a pop-up window the window has the title and the frame already.
  if (bare) return <div data-testid="journal-section" className="flex flex-col gap-2">{body}</div>;
  return (
    <CollapsibleSection title="Journal of visits and treatments" count={entries.length} testId="journal-section" toggleTestId="journal-toggle">
      {body}
    </CollapsibleSection>
  );
}
