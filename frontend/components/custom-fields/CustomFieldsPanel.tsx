"use client";

import { useEffect, useMemo, useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { CustomFieldControl } from "@/components/custom-fields/CustomFieldControl";
import { FieldValue } from "@/components/custom-fields/FieldValue";
import type { ApiError, ApiResult } from "@/lib/api/errors";
import { referenceSearch } from "@/lib/custom-fields/api";
import {
  applyChange,
  buildChanges,
  formDefinitions,
  initialDrafts,
  parentOf,
  parentValueId,
  shownValue,
  type Draft,
  type Drafts,
} from "@/lib/custom-fields/model";
import type { RecordFields } from "@/lib/custom-fields/types";
import { problemsFrom } from "@/lib/forms";

export interface CustomFieldsPanelProps {
  /** The record, its field definitions (metadata) and its saved values. */
  fields: RecordFields;
  /** The page that holds the record says it cannot be edited. The panel does not decide that. */
  readOnly: boolean;
  /** A change elsewhere on the page is running. */
  busy?: boolean;
  /** Messages about fields from outside (a blocked step, say), by field key. Shown at the control. */
  externalErrors?: Record<string, string[]>;
  /**
   * Save the changed values `{key: value or null}` of this record in ONE request. The caller
   * runs it (and decides what happens afterwards); null means "not now" (something else is running).
   */
  save: (values: Record<string, string | boolean | null>) => Promise<ApiResult<unknown> | null>;
  /** Anything that is not a message about a field (a locked record, a missing one, the network, ...). */
  onFailure?: (error: ApiError) => void;
  /** Called while the form is open; the returned function says it closed. */
  registerEditor?: () => () => void;
  /** Heading and the id other parts of the page can link to. */
  title?: string;
  anchorId?: string;
}

/**
 * The custom fields of one record: a read-only list, and (when the page allows editing) a form
 * that saves all changed fields together. Everything comes from field metadata.
 *
 * All changed values go in one request, and so do the values cleared because the field they
 * depended on changed: FastAPI validates a record's values together, and a new parent saved
 * beside the old child would be refused.
 */
export function CustomFieldsPanel(props: CustomFieldsPanelProps) {
  const { fields, readOnly, busy = false, externalErrors = {}, title = "Custom fields", anchorId } = props;
  const [editing, setEditing] = useState(false);

  // If the record stops being editable while the form is open, the form goes away (and does not
  // return when it becomes editable again): adjust the state when that prop changes.
  const [wasReadOnly, setWasReadOnly] = useState(readOnly);
  if (readOnly !== wasReadOnly) {
    setWasReadOnly(readOnly);
    if (readOnly) setEditing(false);
  }

  const definitions = useMemo(() => formDefinitions(fields.definitions), [fields.definitions]);
  if (definitions.length === 0) return null;

  const saved = new Map(fields.values.map((value) => [value.key, value]));

  return (
    <section id={anchorId} aria-label={title} data-testid="custom-fields" data-entity-type={fields.entityType} className="flex flex-col gap-2 scroll-mt-4">
      <h3 className="text-sm font-medium">{title}</h3>
      {editing && !readOnly ? (
        <FieldsForm {...props} onClose={() => setEditing(false)} />
      ) : (
        <>
          <dl className="grid max-w-xl grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
            {definitions.map((definition) => (
              <div key={definition.key} className="contents" data-testid={`cf-${definition.key}`}>
                <dt data-required={definition.required || undefined} className={definition.required ? "after:ml-1 after:text-red-700 after:content-['*'] dark:after:text-red-300" : undefined}>
                  {definition.label}
                </dt>
                <dd>
                  <FieldValue shown={shownValue(definition, saved.get(definition.key))} />
                  {externalErrors[definition.key]?.map((message) => (
                    <p key={message} role="alert" data-testid={`error-${definition.key}`} className="text-sm text-red-700 dark:text-red-300">
                      {message}
                    </p>
                  ))}
                </dd>
              </div>
            ))}
          </dl>
          {!readOnly && (
            <div>
              <Button type="button" disabled={busy} onClick={() => setEditing(true)} data-testid="edit-fields">
                Edit fields
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function FieldsForm({ fields, busy = false, externalErrors = {}, save, onFailure, registerEditor, onClose }: CustomFieldsPanelProps & { onClose: () => void }) {
  const orgId = useOrgId();
  useEffect(() => registerEditor?.(), [registerEditor]);

  const definitions = useMemo(() => formDefinitions(fields.definitions), [fields.definitions]);
  // What the edit is compared with: the saved values as they were when the form opened.
  const [base] = useState<Drafts>(() => initialDrafts(fields.definitions, fields.values));
  const [drafts, setDrafts] = useState<Drafts>(base);
  const [local, setLocal] = useState<Record<string, string[]>>({});
  const [serverErrors, setServerErrors] = useState<{ byField: Record<string, string[]>; general: string[] }>({ byField: {}, general: [] });
  const [saving, setSaving] = useState(false);

  const controls = definitions.map((definition) => `values.${definition.key}`);
  const errorsFor = (key: string) => local[key] ?? serverErrors.byField[`values.${key}`] ?? externalErrors[key];

  const change = (key: string, next: Draft) => setDrafts((current) => applyChange(definitions, current, key, next));

  async function submit() {
    setLocal({});
    setServerErrors({ byField: {}, general: [] });
    const { values, errors } = buildChanges(definitions, base, drafts);
    if (Object.keys(errors).length > 0) {
      setLocal(errors);
      return;
    }
    if (Object.keys(values).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    const result = await save(values);
    setSaving(false);
    if (result === null) return;
    if (result.ok) return onClose();

    if (result.error.kind === "validation") {
      const problems = problemsFrom(result.error, controls);
      setServerErrors({ byField: problems.byField, general: problems.general });
    } else {
      onFailure?.(result.error); // the draft stays, so the user can retry or copy it
    }
  }

  return (
    <form
      aria-label="Edit custom fields"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      data-testid="custom-fields-form"
      className="flex max-w-xl flex-col gap-3"
    >
      {definitions.map((definition) => (
        <FieldRow key={definition.key} orgId={orgId} definitions={definitions} drafts={drafts} definition={definition} error={errorsFor(definition.key)} onChange={(next) => change(definition.key, next)} />
      ))}
      <ErrorSummary messages={serverErrors.general} />
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={busy || saving} data-testid="save-fields">
          {saving ? "Saving…" : "Save fields"}
        </Button>
        <Button type="button" disabled={saving} onClick={onClose} data-testid="cancel-fields">
          Cancel
        </Button>
      </div>
    </form>
  );
}

function FieldRow({
  orgId,
  definitions,
  drafts,
  definition,
  error,
  onChange,
}: {
  orgId: string;
  definitions: ReturnType<typeof formDefinitions>;
  drafts: Drafts;
  definition: ReturnType<typeof formDefinitions>[number];
  error?: string[];
  onChange: (next: Draft) => void;
}) {
  const parent = parentOf(definitions, definition);
  const parentId = parentValueId(definitions, drafts, definition);
  // A NEW search function whenever the parent's value changes: the picker then drops every
  // answer to the old question, and a slow one that still arrives is ignored.
  const search = useMemo(() => (definition.field_type === "reference" ? referenceSearch(orgId, definition, parentId) : undefined), [orgId, definition, parentId]);
  const waitingForParent = parent !== undefined && parentId === null;

  return (
    <CustomFieldControl
      definition={definition}
      draft={drafts[definition.key]}
      onChange={onChange}
      error={error}
      search={search}
      disabled={waitingForParent}
      hint={waitingForParent ? `Choose ${parent.label} first.` : undefined}
    />
  );
}

