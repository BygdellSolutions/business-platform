import { useId, type ReactNode } from "react";

/**
 * Labelled form controls. The control's `name` is the API field name, so a 422 location such
 * as ["body", "price_ex_vat"] reaches the right control with a plain lookup
 * (`fieldErrors[name]`). Errors are tied to the control with aria-describedby / aria-invalid.
 */

export interface FieldProps {
  label: string;
  /** The API field name; also the control's name and the key of its errors. */
  name: string;
  error?: string[];
  hint?: string;
  disabled?: boolean;
  /** Mark the field as required (visual and aria only: the backend decides). */
  required?: boolean;
}

export interface ControlA11y {
  id: string;
  name: string;
  disabled?: boolean;
  "aria-required"?: boolean;
  "aria-invalid": boolean;
  "aria-describedby": string | undefined;
}

const CONTROL = "rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900 aria-[invalid=true]:border-red-600";

/** Label, hint and error around one control (also used by the entity picker). */
export function FieldShell({ label, name, error, hint, disabled, required, children, inline = false }: FieldProps & { children: (a11y: ControlA11y) => ReactNode; inline?: boolean }) {
  const id = useId();
  const hasError = !!error && error.length > 0;
  const describedBy = [hasError ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean).join(" ") || undefined;
  const control = children({ id, name, disabled, "aria-required": required || undefined, "aria-invalid": hasError, "aria-describedby": describedBy });

  return (
    <div className="flex flex-col gap-1">
      {inline ? (
        <label htmlFor={id} className="flex items-center gap-2 text-sm">
          {control} {label}
        </label>
      ) : (
        <>
          <label htmlFor={id} data-required={required || undefined} className={`text-sm font-medium ${required ? "after:ml-1 after:text-red-700 after:content-['*'] dark:after:text-red-300" : ""}`}>
            {label}
          </label>
          {control}
        </>
      )}
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-zinc-500">
          {hint}
        </p>
      )}
      {hasError && (
        <p id={`${id}-error`} data-testid={`error-${name}`} className="text-sm text-red-700 dark:text-red-300">
          {error.join(" ")}
        </p>
      )}
    </div>
  );
}

export function TextField({
  value,
  onChange,
  inputMode,
  autoComplete,
  list,
  ...field
}: FieldProps & {
  value: string;
  onChange: (value: string) => void;
  /** "decimal" shows a numeric keypad on phones; the control stays a text input (see DecimalField). */
  inputMode?: "text" | "decimal" | "email" | "tel";
  autoComplete?: string;
  /** The id of a <datalist> with suggestions. Suggestions only: what may be saved is the backend's decision. */
  list?: string;
}) {
  return (
    <FieldShell {...field}>
      {(a11y) => (
        <input {...a11y} type="text" value={value} inputMode={inputMode} autoComplete={autoComplete} list={list} onChange={(event) => onChange(event.target.value)} className={CONTROL} />
      )}
    </FieldShell>
  );
}

/**
 * For money, VAT and other decimals. A text input on purpose: `type="number"` makes the
 * browser parse, normalise and sometimes reformat the value, which is a numeric conversion we
 * must not have. What the user types is what the form state holds, as a string.
 */
export function DecimalField(props: Omit<Parameters<typeof TextField>[0], "inputMode" | "autoComplete">) {
  return <TextField {...props} inputMode="decimal" autoComplete="off" />;
}

export function TextAreaField({ value, onChange, rows = 3, ...field }: FieldProps & { value: string; onChange: (value: string) => void; rows?: number }) {
  return (
    <FieldShell {...field}>
      {(a11y) => <textarea {...a11y} rows={rows} value={value} onChange={(event) => onChange(event.target.value)} className={CONTROL} />}
    </FieldShell>
  );
}

export function SelectField({
  value,
  onChange,
  options,
  ...field
}: FieldProps & { value: string; onChange: (value: string) => void; options: { value: string; label: string }[] }) {
  return (
    <FieldShell {...field}>
      {(a11y) => (
        <select {...a11y} value={value} onChange={(event) => onChange(event.target.value)} className={CONTROL}>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </FieldShell>
  );
}

export function CheckboxField({ checked, onChange, ...field }: FieldProps & { checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <FieldShell {...field} inline>
      {(a11y) => <input {...a11y} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />}
    </FieldShell>
  );
}
