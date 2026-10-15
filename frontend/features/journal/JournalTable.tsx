import Link from "next/link";

import { SortHeader } from "@/components/ui/SortHeader";
import type { JournalSummary } from "@/lib/api/types";
import type { SortDir } from "@/lib/list-params";
import { formatTimestamp } from "@/lib/timestamps";

/** How a table sorts: the column now in use, its direction, and the address that applies each column's next order. */
export interface JournalSort {
  current: string;
  dir: SortDir;
  hrefs: Record<string, string>;
}

/**
 * The journal's entries as a table like the other lists: No., Visit, By, Status, Marks, Follow-up (the date set for it, if any) and Notes (the start of the text). With `sort`
 * the headings sort the list (the journal page); without it they are plain headings (the section on the horse's page).
 */
export function JournalTable({ orgId, horseId, entries, timeZone, sort }: { orgId: string; horseId: string; entries: JournalSummary[]; timeZone: string | null; sort?: JournalSort }) {
  const base = `/o/${orgId}/horses/${horseId}/journal`;
  const heading = (label: string, key: string, align: "left" | "right" = "left", last = false) =>
    sort ? <SortHeader label={label} sortKey={key} current={sort.current} dir={sort.dir} href={sort.hrefs[key]} align={align} last={last} /> : <th className={`py-1 ${last ? "" : "pr-4"} ${align === "right" ? "text-right" : ""}`}>{label}</th>;
  return (
    <div className="overflow-x-auto">
      <table data-testid="journal-table" className="w-full max-w-5xl text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-300 dark:border-zinc-700">
            {heading("No.", "number", "right")}
            {heading("Visit", "visit")}
            {heading("By", "by")}
            {heading("Status", "status")}
            {heading("Marks", "marks", "right")}
            {heading("Follow-up", "follow_up")}
            <th className="py-1">Notes</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id} data-testid="journal-row" className="border-b border-zinc-200 align-top dark:border-zinc-800">
              <td className="py-1 pr-4 text-right" data-testid="record-number">
                <Link href={`${base}/${entry.id}`} className="underline" data-testid="journal-entry-link">
                  {entry.number}
                </Link>
              </td>
              <td className="py-1 pr-4 whitespace-nowrap" data-testid="entry-visit">
                {formatTimestamp(entry.visit_at, timeZone)}
              </td>
              <td className="py-1 pr-4">{entry.practitioner_name}</td>
              <td className="py-1 pr-4" data-testid="entry-status">
                {entry.status === "draft" ? "Draft" : "Finalized"}
              </td>
              <td className="py-1 pr-4 text-right">{entry.mark_count}</td>
              <td className="py-1 pr-4 whitespace-nowrap" data-testid="entry-follow-up">
                {entry.follow_up_on ?? ""}
              </td>
              <td className="py-1 text-zinc-700 dark:text-zinc-300">{entry.preview}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
