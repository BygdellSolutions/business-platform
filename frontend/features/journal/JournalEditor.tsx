"use client";

import { useRouter } from "next/navigation";
import { useEffect, useReducer, useState, type KeyboardEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { CONTROL } from "@/components/ui/controls";
import { FieldShell, TextAreaField } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api/client";
import type { Colleague, JournalEntry, JournalUpdate, MarkSide, MarkTool } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";
import { DiagramCanvas, type ActiveTool } from "./DiagramCanvas";
import { describe, editorReducer, initialState, numberOf, sameMarks, SIDE_LABELS, TOOL_LABELS } from "./marks";

const CONTROLS = ["visit_at", "practitioner_user_id", "body", "follow_up_on", "marks"] as const;
const TOOLS: { tool: ActiveTool; label: string }[] = [
  { tool: "select", label: "Select / move" },
  ...(["line", "arrow", "x", "o"] as MarkTool[]).map((tool) => ({ tool, label: TOOL_LABELS[tool] })),
];

/** An ISO instant as the value of a `datetime-local` input (the browser's own time zone). */
function toLocalInput(iso: string): string {
  const date = new Date(iso);
  const part = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${part(date.getMonth() + 1)}-${part(date.getDate())}T${part(date.getHours())}:${part(date.getMinutes())}`;
}

/**
 * Writing a journal entry (a draft): when and who, one horse diagram per side with drawing marks, and one text. Nothing is saved
 * until "Save draft"; the page says plainly when there are unsaved changes and asks the browser before leaving with any. The draft is
 * saved against the version it was opened on, so a change made elsewhere is refused (nothing overwritten) and shown here.
 */
export function JournalEditor({ entry }: { entry: JournalEntry }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [saved, setSaved] = useState(entry);
  const [visitAt, setVisitAt] = useState(toLocalInput(entry.visit_at));
  const [practitioner, setPractitioner] = useState(entry.practitioner_user_id ?? "");
  const [colleagues, setColleagues] = useState<Colleague[]>([]);
  const [body, setBody] = useState(entry.body);
  const [followUp, setFollowUp] = useState(entry.follow_up_on ?? "");
  const [editor, dispatch] = useReducer(editorReducer, entry.marks, initialState);
  const [side, setSide] = useState<MarkSide>("left");
  const [tool, setTool] = useState<ActiveTool>("select");
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const dirty =
    visitAt !== toLocalInput(saved.visit_at) ||
    practitioner !== (saved.practitioner_user_id ?? "") ||
    body !== saved.body ||
    followUp !== (saved.follow_up_on ?? "") ||
    !sameMarks(editor.marks, saved.marks);

  useEffect(() => {
    const controller = new AbortController();
    void apiFetch<Colleague[]>(orgId, "/members/people?journal=true", { signal: controller.signal }).then((result) => {
      if (result.ok) setColleagues(result.data);
    });
    return () => controller.abort();
  }, [orgId]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const problems = problemsFrom(error, CONTROLS);
  const stale = error?.kind === "conflict" && error.code === "stale_record";
  const finalError = error?.kind === "conflict" && error.code === "journal_entry_final";

  function changes(): JournalUpdate {
    const update: JournalUpdate = {};
    if (visitAt !== toLocalInput(saved.visit_at) && visitAt !== "") update.visit_at = new Date(visitAt).toISOString();
    if (practitioner !== (saved.practitioner_user_id ?? "") && practitioner !== "") update.practitioner_user_id = practitioner;
    if (body !== saved.body) update.body = body;
    if (followUp !== (saved.follow_up_on ?? "")) update.follow_up_on = followUp === "" ? null : followUp;
    if (!sameMarks(editor.marks, saved.marks)) update.marks = editor.marks;
    return update;
  }

  async function save(): Promise<JournalEntry | null> {
    setNotice(null);
    const update = changes();
    if (Object.keys(update).length === 0) return saved;
    const result = await run(() => apiFetch<JournalEntry>(orgId, `/horses/${entry.horse_id}/journal/${entry.id}`, { method: "PATCH", body: update, ifMatch: saved.version }));
    if (result === null) return null;
    setSaved(result);
    setNotice("Draft saved.");
    return result;
  }

  async function finalize() {
    const current = await save();
    if (current === null) return;
    const result = await run(() => apiFetch<JournalEntry>(orgId, `/horses/${entry.horse_id}/journal/${entry.id}/finalize`, { method: "POST", ifMatch: current.version }));
    if (result === null) return;
    router.refresh(); // the page now shows the finalized entry
  }

  function onKeyDown(event: KeyboardEvent) {
    const typing = event.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);
    if (typing) return;
    if ((event.key === "Delete" || event.key === "Backspace") && selected !== null) {
      event.preventDefault();
      dispatch({ type: "remove", id: selected });
      setSelected(null);
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      dispatch({ type: event.shiftKey ? "redo" : "undo" });
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
      event.preventDefault();
      dispatch({ type: "redo" });
    }
  }

  const marksHere = editor.marks.filter((mark) => mark.side === side);
  const status = pending ? "Saving…" : dirty ? "Unsaved changes" : "All changes saved";

  return (
    <div className="flex max-w-3xl flex-col gap-4" data-testid="journal-editor" onKeyDown={onKeyDown}>
      <div className="panel flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Visit</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <FieldShell label="Visit date and time" name="visit_at" error={problems.byField.visit_at}>
            {(control) => <input type="datetime-local" {...control} value={visitAt} onChange={(event) => setVisitAt(event.target.value)} className={CONTROL} />}
          </FieldShell>
          <FieldShell label="Practitioner" name="practitioner_user_id" error={problems.byField.practitioner_user_id}>
            {(control) => (
              <select {...control} value={practitioner} onChange={(event) => setPractitioner(event.target.value)} className={CONTROL}>
                {saved.practitioner_user_id === null && <option value="">Choose a practitioner</option>}
                {colleagues.length === 0 && saved.practitioner_user_id !== null && <option value={saved.practitioner_user_id}>{saved.practitioner_name}</option>}
                {colleagues.map((colleague) => (
                  <option key={colleague.user_id} value={colleague.user_id}>
                    {colleague.name}
                  </option>
                ))}
              </select>
            )}
          </FieldShell>
          <FieldShell label="Follow-up date (optional)" name="follow_up_on" error={problems.byField.follow_up_on}>
            {(control) => <input type="date" {...control} value={followUp} onChange={(event) => setFollowUp(event.target.value)} className={CONTROL} />}
          </FieldShell>
        </div>
        <p className="text-xs text-zinc-500">The visit time is in your own time zone.</p>
      </div>

      <div className="panel flex flex-col gap-3">
        <h2 className="text-lg font-semibold">Diagram</h2>
        <div role="group" aria-label="Side of the horse" className="flex gap-2">
          {(["left", "right"] as MarkSide[]).map((candidate) => (
            <Button key={candidate} type="button" aria-pressed={side === candidate} onClick={() => setSide(candidate)} data-testid={`side-${candidate}`} variant={side === candidate ? "primary" : "secondary"}>
              {SIDE_LABELS[candidate]}
            </Button>
          ))}
        </div>
        <div role="toolbar" aria-label="Drawing tools" className="flex flex-wrap gap-2">
          {TOOLS.map((item) => (
            <Button key={item.tool} type="button" aria-pressed={tool === item.tool} onClick={() => setTool(item.tool)} data-testid={`tool-${item.tool}`} variant={tool === item.tool ? "primary" : "secondary"}>
              {item.label}
            </Button>
          ))}
          <Button type="button" disabled={selected === null} onClick={() => { if (selected !== null) dispatch({ type: "remove", id: selected }); setSelected(null); }} data-testid="delete-mark">
            Delete selected
          </Button>
          <Button type="button" disabled={editor.past.length === 0} onClick={() => dispatch({ type: "undo" })} data-testid="undo">
            Undo
          </Button>
          <Button type="button" disabled={editor.future.length === 0} onClick={() => dispatch({ type: "redo" })} data-testid="redo">
            Redo
          </Button>
        </div>
        <DiagramCanvas
          side={side}
          marks={editor.marks}
          version={entry.diagram_version}
          editable
          tool={tool}
          selectedId={selected}
          onSelect={setSelected}
          onAdd={(mark) => dispatch({ type: "add", mark })}
          onMove={(mark) => dispatch({ type: "replace", mark })}
        />
        <ErrorSummary messages={problems.byField.marks ?? []} />
        <div>
          <h3 className="text-sm font-medium">Marks on the {side} side</h3>
          {marksHere.length === 0 ? (
            <p className="text-sm text-zinc-500" data-testid="no-marks">
              No marks yet. Pick a tool and draw on the diagram.
            </p>
          ) : (
            <ol className="flex flex-col gap-1 text-sm" data-testid="marks-list">
              {marksHere.map((mark) => (
                <li key={mark.id} className="flex items-center gap-2">
                  <button type="button" className={`text-left underline ${mark.id === selected ? "font-semibold" : ""}`} onClick={() => { setTool("select"); setSelected(mark.id); }}>
                    {numberOf(mark, editor.marks)}. {describe(mark)}
                  </button>
                  <Button type="button" onClick={() => { dispatch({ type: "remove", id: mark.id }); if (selected === mark.id) setSelected(null); }} aria-label={`Delete mark ${numberOf(mark, editor.marks)}`} data-testid="delete-listed-mark">
                    Delete
                  </Button>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      <div className="panel flex flex-col gap-3">
        <TextAreaField label="Observations, assessment, treatment, response, advice and follow-up" name="body" rows={10} value={body} onChange={setBody} error={problems.byField.body} />
        <p className="text-xs text-zinc-500">{body.length.toLocaleString("en")} of 20,000 characters. Refer to a mark by its number, for example “mark 2”.</p>
      </div>

      <ErrorSummary messages={problems.general} />
      {stale && (
        <div role="alert" data-testid="stale" className="flex flex-col gap-2 rounded border border-amber-400 p-3 text-sm">
          <p>This entry was changed somewhere else, so nothing was saved. Reload to see the latest version (your unsaved changes here are lost).</p>
          <div>
            <Button type="button" onClick={() => router.refresh()}>
              Reload
            </Button>
          </div>
        </div>
      )}
      {finalError && <p role="alert">This entry has been finalized elsewhere and can no longer be changed.</p>}
      {error && !stale && !finalError && error.kind !== "validation" && <p role="alert">{error.message}</p>}
      {notice && !dirty && (
        <p role="status" data-testid="saved">
          {notice}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="primary" disabled={pending || !dirty} onClick={() => void save()} data-testid="save-draft">
          Save draft
        </Button>
        <ConfirmButton
          label="Finalize entry"
          question="Finalize this entry? Its text and marks are then frozen; later corrections are added as dated notes."
          confirmLabel="Yes, finalize"
          disabled={pending}
          onConfirm={() => void finalize()}
          testId="finalize"
        />
        <span role="status" aria-live="polite" data-testid="save-status" className={dirty ? "text-sm font-medium text-amber-700" : "text-sm text-zinc-500"}>
          {status}
        </span>
      </div>
    </div>
  );
}
