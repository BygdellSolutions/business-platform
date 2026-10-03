"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { FieldShell, type FieldProps } from "@/components/ui/Field";
import type { ApiResult } from "@/lib/api/errors";

/**
 * Picks ONE record of some kind (a customer, later an item or a custom-field reference).
 *
 * The picker knows nothing about customers, organizations or the backend: the caller says how
 * to search (`search`) and what is selected now (`value`). What it guarantees:
 *   - the selection is an entity with an id; typing text never changes it, only choosing an
 *     option does, and the id (never the text) is what the caller submits;
 *   - choices are loaded when the list opens and again for every change of the text; an older
 *     request is aborted and its answer is ignored if it still arrives, so a slow answer can
 *     never overwrite a newer one (or fill a picker that has been closed or removed);
 *   - the current selection is always displayed, including one that is no longer offered
 *     (`inactive`), and is not cleared unless the user clears it;
 *   - loading, empty and error states are shown, and it works with the keyboard (combobox
 *     pattern: arrows move, Enter chooses, Escape closes).
 *
 * It holds no tenant data outside its own state, so removing it (the organization scope does
 * that when the organization changes) removes every result it had.
 */

export interface PickerEntity {
  id: string;
  label: string;
  /** Secondary text in the list (an email, a unit). Never used as identity. */
  detail?: string;
  /** Shown as "(inactive)" next to the label. */
  inactive?: boolean;
}

export type PickerSearch = (query: string, signal: AbortSignal) => Promise<ApiResult<PickerEntity[]>>;

type Answer = { key: string } & ({ status: "ready"; entities: PickerEntity[] } | { status: "error"; message: string });

const CONTROL = "w-full rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900 aria-[invalid=true]:border-red-600";

function display(entity: PickerEntity | null): string {
  if (entity === null) return "";
  return entity.inactive ? `${entity.label} (inactive)` : entity.label;
}

export function EntityPicker({
  label,
  name,
  error,
  hint,
  disabled,
  readOnly = false,
  value,
  onChange,
  search,
  clearable = false,
  placeholder = "Type to search",
}: FieldProps & {
  /** The current selection (id and what to show for it), or null. */
  value: PickerEntity | null;
  onChange: (entity: PickerEntity | null) => void;
  search: PickerSearch;
  /** Offer a way to clear the selection (for optional relationships). */
  clearable?: boolean;
  readOnly?: boolean;
  placeholder?: string;
}) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState<string | null>(null); // null: showing the selection, not typing
  const [reload, setReload] = useState(0);
  const [session, setSession] = useState(0); // a new one each time the list opens: no choices carry over
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [cursor, setCursor] = useState<{ key: string; index: number }>({ key: "", index: -1 });

  const term = (query ?? "").trim();
  const key = `${session}:${term}#${reload}`;
  // An answer counts only for the request it was made for; anything else means "loading".
  const current = answer !== null && answer.key === key ? answer : null;
  const entities = current?.status === "ready" ? current.entities : [];
  const index = cursor.key === key && cursor.index < entities.length ? cursor.index : -1;
  const interactive = !disabled && !readOnly;

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    search(term, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return; // a late answer to a question nobody is asking any more
        setAnswer(result.ok ? { key, status: "ready", entities: result.data } : { key, status: "error", message: result.error.message });
      })
      .catch(() => {
        /* aborted: nothing to show */
      });
    return () => controller.abort();
  }, [open, term, key, search]);

  function openList() {
    if (open) return;
    setSession((count) => count + 1);
    setOpen(true);
  }

  function choose(entity: PickerEntity) {
    onChange(entity);
    setOpen(false);
    setQuery(null);
  }

  function close() {
    setOpen(false);
    setQuery(null);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!interactive) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openList();
        return;
      }
      if (entities.length === 0) return;
      const next = event.key === "ArrowDown" ? Math.min(index + 1, entities.length - 1) : Math.max(index - 1, 0);
      setCursor({ key, index: next });
    } else if (event.key === "Enter" && open && index >= 0) {
      event.preventDefault(); // choosing an option must not also submit the surrounding form
      choose(entities[index]);
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  }

  return (
    <FieldShell label={label} name={name} error={error} hint={hint} disabled={disabled}>
      {(a11y) => (
        <div
          data-testid={`picker-${name}`}
          className="relative"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) close();
          }}
        >
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              id={a11y.id}
              aria-invalid={a11y["aria-invalid"]}
              aria-describedby={a11y["aria-describedby"]}
              role="combobox"
              aria-expanded={open}
              aria-controls={listId}
              aria-autocomplete="list"
              aria-activedescendant={open && index >= 0 ? `${listId}-${index}` : undefined}
              aria-readonly={readOnly || undefined}
              autoComplete="off"
              disabled={disabled}
              readOnly={readOnly}
              placeholder={value === null ? placeholder : undefined}
              value={query ?? display(value)}
              onChange={(event) => {
                setQuery(event.target.value);
                openList();
              }}
              onFocus={() => interactive && openList()}
              onClick={() => interactive && openList()}
              onKeyDown={onKeyDown}
              className={CONTROL}
            />
            {clearable && interactive && value !== null && (
              <button
                type="button"
                aria-label={`Clear ${label}`}
                onClick={() => {
                  onChange(null);
                  setQuery(null);
                  inputRef.current?.focus();
                }}
                className="rounded border border-zinc-400 px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                Clear
              </button>
            )}
          </div>

          {/* What a plain GET form submits: the id, never the text. */}
          <input type="hidden" name={name} value={value?.id ?? ""} />

          {open && (
            <div className="absolute z-10 mt-1 w-full rounded border border-zinc-300 bg-white shadow dark:border-zinc-700 dark:bg-zinc-900">
              <ul id={listId} role="listbox" aria-label={`${label} choices`} className="max-h-60 overflow-auto">
                {entities.map((entity, position) => (
                  <li
                    key={entity.id}
                    id={`${listId}-${position}`}
                    role="option"
                    aria-selected={entity.id === value?.id}
                    data-testid="picker-option"
                    ref={(element) => {
                      if (position === index) element?.scrollIntoView?.({ block: "nearest" });
                    }}
                    onMouseDown={(event) => event.preventDefault()} // keep focus in the input
                    onClick={() => choose(entity)}
                    className={`cursor-pointer px-2 py-1 text-sm ${position === index ? "bg-zinc-200 dark:bg-zinc-700" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}
                  >
                    {display(entity)}
                    {entity.detail && <span className="ml-2 text-xs text-zinc-500">{entity.detail}</span>}
                  </li>
                ))}
              </ul>
              {current === null && (
                <p role="status" data-testid="picker-status" className="px-2 py-1 text-sm text-zinc-500">
                  Searching…
                </p>
              )}
              {current?.status === "ready" && entities.length === 0 && (
                <p role="status" data-testid="picker-status" className="px-2 py-1 text-sm text-zinc-500">
                  No matches
                </p>
              )}
              {current?.status === "error" && (
                <div role="alert" data-testid="picker-error" className="flex items-center gap-2 px-2 py-1 text-sm text-red-700 dark:text-red-300">
                  <span>{current.message}</span>
                  <button
                    type="button"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => setReload((count) => count + 1)}
                    className="underline"
                  >
                    Try again
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </FieldShell>
  );
}
