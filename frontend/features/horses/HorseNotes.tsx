"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextAreaField } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api/client";
import type { HorseNote } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";
import { formatTimestamp } from "@/lib/timestamps";

const CONTROLS = ["body"] as const;

/**
 * Notes on a horse, newest first: what was seen, done or agreed, with who wrote it and when. Members who may change
 * records add, edit and delete notes (each step is in the horse's history); everyone else reads them.
 */
export function HorseNotes({ horseId, notes, canWrite, timeZone }: { horseId: string; notes: HorseNote[]; canWrite: boolean; timeZone: string | null }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null);
  const problems = problemsFrom(error, CONTROLS);

  async function add(event: FormEvent) {
    event.preventDefault();
    const saved = await run(() => apiFetch<HorseNote>(orgId, `/horses/${horseId}/notes`, { method: "POST", body: { body: draft } }));
    if (saved === null) return;
    setDraft("");
    router.refresh();
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (editing === null) return;
    const saved = await run(() => apiFetch<HorseNote>(orgId, `/horses/${horseId}/notes/${editing.id}`, { method: "PATCH", body: { body: editing.body } }));
    if (saved === null) return;
    setEditing(null);
    router.refresh();
  }

  async function remove(id: string) {
    const removed = await run(() => apiFetch<null>(orgId, `/horses/${horseId}/notes/${id}`, { method: "DELETE" }));
    if (removed !== null) router.refresh();
  }

  return (
    <section aria-label="Notes" data-testid="horse-notes" className="flex max-w-3xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Notes</h2>
      {canWrite && (
        <form onSubmit={add} noValidate aria-label="Add note" className="flex flex-col gap-2">
          <TextAreaField label="New note" name="body" value={draft} onChange={setDraft} error={editing === null ? problems.byField.body : undefined} />
          <div>
            <Button type="submit" disabled={pending || draft.trim() === ""} data-testid="add-note">
              {pending ? "Saving…" : "Add note"}
            </Button>
          </div>
        </form>
      )}
      <ErrorSummary messages={problems.general} />
      {notes.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-notes">
          No notes yet.
        </p>
      ) : (
        // Collapsed until asked for, so a long log never pushes the page down; adding a note above stays open.
        <details data-testid="notes-toggle">
          <summary className="cursor-pointer select-none text-sm text-zinc-700 underline dark:text-zinc-300">Show notes ({notes.length})</summary>
        <ul className="mt-2 flex flex-col gap-2">
          {notes.map((note) => (
            <li key={note.id} data-testid="horse-note" className="rounded border border-zinc-200 p-3 dark:border-zinc-800">
              <div className="mb-1 text-xs text-zinc-500">
                {formatTimestamp(note.created_at, timeZone)} · {note.created_by_name ?? "not recorded"}
                {note.updated_at !== note.created_at && note.updated_by && (
                  <> · changed {formatTimestamp(note.updated_at, timeZone)} by {note.updated_by_name ?? "not recorded"}</>
                )}
              </div>
              {editing?.id === note.id ? (
                <form onSubmit={save} noValidate aria-label="Edit note" className="flex flex-col gap-2">
                  <TextAreaField label="Note" name="body" value={editing.body} onChange={(body) => setEditing({ id: note.id, body })} error={problems.byField.body} />
                  <div className="flex gap-2">
                    <Button type="submit" disabled={pending || editing.body.trim() === ""} data-testid="save-note">
                      Save
                    </Button>
                    <Button type="button" disabled={pending} onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                  </div>
                </form>
              ) : (
                <>
                  <p className="whitespace-pre-wrap text-sm" data-testid="horse-note-body">
                    {note.body}
                  </p>
                  {canWrite && (
                    <div className="mt-2 flex gap-2">
                      <Button type="button" disabled={pending} onClick={() => setEditing({ id: note.id, body: note.body })} data-testid="edit-note">
                        Edit
                      </Button>
                      <ConfirmButton label="Delete" question="Delete this note? It stays in the horse's history." confirmLabel="Yes, delete" disabled={pending} onConfirm={() => void remove(note.id)} testId="delete-note" />
                    </div>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
        </details>
      )}
    </section>
  );
}
