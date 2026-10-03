import { useId, type ReactNode } from "react";

/**
 * Labelled form controls. The control's `name` is the API field name, so a 422 location such
 * as ["body", "price_ex_vat"] reaches the right control with a plain lookup
 * (`fieldErrors[name]`). Errors are tied to the control with aria-describedby / aria-invalid.
 */

interface FieldProps {
  label: string;
  /** The API field name; also the control's name and the key of its errors. */
  name: string;
  error?: string[];
  hint?: string;
  disabled?: boolean;
}

interface ControlA11y {
  id: string;
  name: string;
  disabled?: boolean;
  "aria-invalid": boolean;
  "aria-describedby": string | undefined;
}

const CONTROL = "rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900 aria-[invalid=true]:border-red-600";

function Shell({ label, name, error, hint, disabled, children, inline = false }: FieldProps & { children: (a11y: ControlA11y) => ReactNode; inline?: boolean }) {
  const id = useId();
  const hasError = !!error && error.length > 0;
  const describedBy = [hasError ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean).join(" ") || undefined;
  const control = children({ id, name, disabled, "aria-invalid": hasError, "aria-describedby": describedBy });

  return (
    <div className="flex flex-col gap-1">
      {inline ? (
        <label htmlFor={id} className="flex items-center gap-2 text-sm">
          {control} {label}
        </label>
      ) : (
        <>
          <label htmlFor={id} className="text-sm font-medium">
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
  ...field
}: FieldProps & {
  value: string;
  onChange: (value: string) => void;
  /** "decimal" shows a numeric keypad on phones; the control stays a text input (see DecimalField). */
  inputMode?: "text" | "decimal" | "email" | "tel";
  autoComplete?: string;
}) {
  return (
    <Shell {...field}>
      {(a11y) => (
        <input {...a11y} type="text" value={value} inputMode={inputMode} autoComplete={autoComplete} onChange={(event) => onChange(event.target.value)} className={CONTROL} />
      )}
    </Shell>
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
    <Shell {...field}>
      {(a11y) => <textarea {...a11y} rows={rows} value={value} onChange={(event) => onChange(event.target.value)} className={CONTROL} />}
    </Shell>
  );
}

export function SelectField({
  value,
  onChange,
  options,
  ...field
}: FieldProps & { value: string; onChange: (value: string) => void; options: { value: string; label: string }[] }) {
  return (
    <Shell {...field}>
      {(a11y) => (
        <select {...a11y} value={value} onChange={(event) => onChange(event.target.value)} className={CONTROL}>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </Shell>
  );
}

export function CheckboxField({ checked, onChange, ...field }: FieldProps & { checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <Shell {...field} inline>
      {(a11y) => <input {...a11y} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />}
    </Shell>
  );
}
