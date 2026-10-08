/**
 * What a service line says about the work, in one line: for whom, when and by whom, then the notes. Only text that
 * the backend sent (live names on a transaction, the snapshot on an invoice); nothing is looked up here.
 */
export function ServiceSummary({ subject, when, by, notes }: { subject: string | null; when: string; by: string | null; notes: string | null }) {
  return (
    <span className="block text-xs text-zinc-600 dark:text-zinc-400" data-testid="service-summary">
      Service for <span className="font-medium">{subject ?? "a record that no longer exists"}</span> · {when}
      {by ? ` · by ${by}` : ""}
      {notes && <span className="block italic" data-testid="service-notes">{notes}</span>}
    </span>
  );
}
