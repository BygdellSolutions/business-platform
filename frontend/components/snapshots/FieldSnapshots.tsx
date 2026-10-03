import type { FieldSnapshot } from "@/lib/api/types";

/**
 * Shows stored custom-field values: a read-only list of label and text.
 *
 * Input is ONLY the stored snapshot array. It never asks for definitions, choices or records, so
 * it renders the same after every live field has been renamed, disabled or removed. A field's
 * type decides how its stored value is presented; what a field MEANS is not known here, and there
 * is no special case for any particular field. The order is the stored order.
 *
 *   text, number, date  the stored text (a number is a decimal STRING, never converted)
 *   boolean             Yes / No (the stored state)
 *   select, reference   the stored display text; a target that was already gone when the snapshot
 *                       was taken is shown as such
 */
export const GONE = "(no longer existed)";

export function presentSnapshot(field: FieldSnapshot): string {
  switch (field.field_type) {
    case "boolean":
      return field.value === true ? "Yes" : field.value === false ? "No" : "";
    case "select":
    case "reference":
      if (field.missing) return GONE;
      return field.display ?? "";
    default:
      return field.display ?? (typeof field.value === "string" ? field.value : "");
  }
}

export function FieldSnapshots({ fields, label, testId }: { fields: FieldSnapshot[]; label: string; testId?: string }) {
  if (fields.length === 0) return null;
  return (
    <dl aria-label={label} data-testid={testId} className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
      {fields.map((field) => (
        <div key={`${field.definition_id}-${field.key}`} className="contents" data-testid="snapshot-field" data-field-type={field.field_type} data-missing={field.missing || undefined}>
          <dt className="text-zinc-600 dark:text-zinc-400">{field.label}</dt>
          <dd>{presentSnapshot(field)}</dd>
        </div>
      ))}
    </dl>
  );
}
