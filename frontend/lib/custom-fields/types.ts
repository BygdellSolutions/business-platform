/**
 * Custom Fields, as the backend describes them (`/api/custom-fields`). Hand-written, like the
 * other API types.
 *
 * This module and everything under `lib/custom-fields` and `components/custom-fields` is
 * GENERIC: it understands field metadata and six value types, and nothing about what any
 * organization or module uses them for. A test enforces that (see boundary.test.ts).
 */

export type FieldType = "text" | "number" | "date" | "boolean" | "select" | "reference";

export interface OptionRead {
  id: string;
  label: string;
  position: number;
  enabled: boolean;
}

export interface ReferenceRead {
  /** Which registered entity the field points at. Opaque to the frontend. */
  source: string;
  /** Key of the field of the same record this one depends on, if any. */
  depends_on: string | null;
  filter: string | null;
}

export interface Definition {
  id: string;
  entity_type: string;
  key: string;
  label: string;
  field_type: FieldType;
  required: boolean;
  position: number;
  enabled: boolean;
  show_in_form: boolean;
  show_in_table: boolean;
  show_on_invoice: boolean;
  reference: ReferenceRead | null;
  /** Select fields only. */
  options: OptionRead[] | null;
  created_at: string;
  updated_at: string;
}

/**
 * A value that IS set. A field with no value has no entry at all; that is how "unset" is
 * told apart from `false` for booleans.
 *
 * `value` is what is stored: text, a decimal STRING for numbers (never a JavaScript number),
 * `YYYY-MM-DD` for dates, `true`/`false`, or the UUID of an option or a referenced record.
 * `display`, `active` and `missing` are resolved live by the backend for options and references.
 */
export interface ValueRead {
  key: string;
  label: string;
  field_type: FieldType;
  value: unknown;
  display: string | null;
  /** Options and references: is the target still selectable for a NEW assignment? */
  active: boolean | null;
  /** A reference whose target no longer exists (or an option that was removed). */
  missing: boolean;
}

export interface BulkValuesRead {
  entity_type: string;
  entities: Record<string, ValueRead[]>;
}

export interface Choice {
  id: string;
  label: string;
  active: boolean;
}

/** The definitions and values of ONE record, as the page passes them to the generic panel. */
export interface RecordFields {
  entityType: string;
  entityId: string;
  definitions: Definition[];
  values: ValueRead[];
}
