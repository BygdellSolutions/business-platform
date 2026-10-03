"use client";

import { EntityPicker, type PickerSearch } from "@/components/ui/EntityPicker";
import { DecimalField, FieldShell, SelectField, TextField } from "@/components/ui/Field";
import type { Draft, RefDraft } from "@/lib/custom-fields/model";
import type { Definition } from "@/lib/custom-fields/types";

const CONTROL = "rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900 aria-[invalid=true]:border-red-600";
const NOT_SET = { value: "", label: "Not set" };

/**
 * The input for ONE custom field, chosen from its metadata type alone. It knows nothing about
 * what the field is for. `required` is only a marker: the backend decides what is required.
 *
 *   text       a text box
 *   number     a text box whose content stays a string (never `type=number`, never parsed)
 *   date       a date input (`YYYY-MM-DD`)
 *   boolean    a three-way choice: Not set / Yes / No
 *   select     the backend's options (a disabled option that is already chosen stays visible)
 *   reference  the entity picker, fed by `search` (the generic choices of the definition)
 */
export function CustomFieldControl({
  definition,
  draft,
  onChange,
  error,
  disabled,
  hint,
  search,
}: {
  definition: Definition;
  draft: Draft;
  onChange: (next: Draft) => void;
  error?: string[];
  disabled?: boolean;
  hint?: string;
  /** Required for reference fields. */
  search?: PickerSearch;
}) {
  const common = { label: definition.label, name: definition.key, required: definition.required, error, disabled, hint };

  switch (draft.type) {
    case "text":
      return <TextField {...common} value={draft.value} onChange={(value) => onChange({ type: "text", value })} autoComplete="off" />;

    case "number":
      return <DecimalField {...common} value={draft.value} onChange={(value) => onChange({ type: "number", value })} />;

    case "date":
      return (
        <FieldShell {...common}>
          {(a11y) => (
            <input {...a11y} type="date" value={draft.value} onChange={(event) => onChange({ type: "date", value: event.target.value })} className={CONTROL} />
          )}
        </FieldShell>
      );

    case "boolean":
      return (
        <SelectField
          {...common}
          value={draft.value === null ? "" : draft.value ? "true" : "false"}
          onChange={(value) => onChange({ type: "boolean", value: value === "" ? null : value === "true" })}
          options={[NOT_SET, { value: "true", label: "Yes" }, { value: "false", label: "No" }]}
        />
      );

    case "select": {
      const options = (definition.options ?? []).filter((option) => option.enabled).map((option) => ({ value: option.id, label: option.label }));
      const current: RefDraft | null = draft.value;
      // A chosen option that was disabled since still has to be shown, or the control would lie.
      if (current && !options.some((option) => option.value === current.id)) options.push({ value: current.id, label: `${current.label} (disabled)` });
      return (
        <SelectField
          {...common}
          value={current?.id ?? ""}
          onChange={(id) => {
            const chosen = id === "" ? null : (definition.options ?? []).find((option) => option.id === id);
            onChange({ type: "select", value: id === "" ? null : chosen ? { id: chosen.id, label: chosen.label, inactive: !chosen.enabled } : current });
          }}
          options={[NOT_SET, ...options]}
        />
      );
    }

    case "reference":
      return (
        <EntityPicker
          {...common}
          value={draft.value && { id: draft.value.id, label: draft.value.label, inactive: draft.value.inactive }}
          onChange={(entity) => onChange({ type: "reference", value: entity && { id: entity.id, label: entity.label, inactive: !!entity.inactive } })}
          search={search ?? NO_CHOICES}
          clearable
        />
      );
  }
}

const NO_CHOICES: PickerSearch = async () => ({ ok: true, status: 200, data: [] });
