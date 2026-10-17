"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextAreaField } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api/client";
import type { JournalEntry } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";
import { formatDay } from "@/lib/timestamps";
import { DiagramCanvas } from "./DiagramCanvas";
import { describe, numberOf, SIDE_LABELS } from "./marks";

/**
 * A finalized entry: exactly what was frozen (text and marks on both sides of the horse), who finalized it and when, and the dated
 * corrections added since. The author, an admin or an owner may add a correction; nothing already written can be changed.
 */
export function JournalEntryView({ entry, timeZone }: { entry: JournalEntry; timeZone: string | null }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [text, setText] = useState("");
  const problems = problemsFrom(error, ["body"] as const);

  async function addCorrection(event: FormEvent) {
    event.preventDefault();
    const saved = await run(() => apiFetch<JournalEntry>(orgId, `/horses/${entry.horse_id}/journal/${entry.id}/corrections`, { method: "POST", body: { body: text } }));
    if (saved === null) return;
    setText("");
    router.refresh();
  }

  return (
    <div className="flex max-w-3xl flex-col gap-4" data-testid="journal-entry">
      <div className="panel flex flex-col gap-1 text-sm">
        <p data-testid="entry-visit">
          <strong>Visit:</strong> {formatDay(entry.visit_at, timeZone)} · <strong>Practitioner:</strong> {entry.practitioner_name}
        </p>
        {entry.order_id && (
          <p data-testid="entry-order">
            <strong>Order:</strong>{" "}
            {entry.order_number !== null ? (
              <Link href={`/o/${orgId}/transactions/${entry.order_id}`} className="underline">
                Order {entry.order_number}
              </Link>
            ) : (
              <span className="text-zinc-500">the linked order no longer exists</span>
            )}
          </p>
        )}
        {entry.follow_up_on && (
          <p data-testid="entry-follow-up">
            <strong>Follow-up:</strong> {entry.follow_up_on}
          </p>
        )}
        {entry.status === "final" ? (
          <p data-testid="entry-final" className="text-zinc-600 dark:text-zinc-400">
            Finalized {entry.finalized_at ? formatDay(entry.finalized_at, timeZone) : ""} by {entry.finalized_by_name ?? "someone no longer recorded"}. It can no longer be changed.
          </p>
        ) : (
          <p data-testid="entry-draft" className="text-zinc-600 dark:text-zinc-400">
            A draft: not finalized yet.
          </p>
        )}
      </div>

      <div className="panel flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Diagram</h2>
        {(["left", "right"] as const).map((side) => (
          <div key={side} className="flex flex-col gap-1">
            <h3 className="text-sm font-medium">{SIDE_LABELS[side]}</h3>
            <DiagramCanvas side={side} marks={entry.marks} version={entry.diagram_version} />
            <ul className="text-sm">
              {entry.marks.filter((mark) => mark.side === side).map((mark) => (
                <li key={mark.id}>
                  {numberOf(mark, entry.marks)}. {describe(mark)}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="panel flex flex-col gap-2">
        <h2 className="text-lg font-semibold">Notes</h2>
        <p className="whitespace-pre-wrap" data-testid="entry-body">
          {entry.body.trim() === "" ? "No text." : entry.body}
        </p>
      </div>

      <section aria-label="Corrections" data-testid="corrections" className="panel flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Corrections</h2>
        {entry.corrections.length === 0 ? (
          <p className="text-sm text-zinc-500" data-testid="no-corrections">
            No corrections.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {entry.corrections.map((correction) => (
              <li key={correction.id} data-testid="correction" className="text-sm">
                <p className="text-xs text-zinc-500">
                  {formatDay(correction.created_at, timeZone)} · {correction.created_by_name ?? "not recorded"}
                </p>
                <p className="whitespace-pre-wrap">{correction.body}</p>
              </li>
            ))}
          </ul>
        )}
        {entry.can_correct && (
          <form onSubmit={addCorrection} noValidate aria-label="Add correction" className="flex flex-col gap-2">
            <TextAreaField label="Add a correction (dated; the entry above stays as it was)" name="body" value={text} onChange={setText} error={problems.byField.body} />
            <ErrorSummary messages={problems.general} />
            <div>
              <Button type="submit" disabled={pending || text.trim() === ""} data-testid="add-correction">
                {pending ? "Saving…" : "Add correction"}
              </Button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
