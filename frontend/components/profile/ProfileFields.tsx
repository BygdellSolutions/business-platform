"use client";

import { TextField } from "@/components/ui/Field";
import type { FieldErrors } from "@/lib/api/errors";
import type { ProfileField } from "@/lib/api/types";
import { PROFILE_FIELDS, PROFILE_LABELS, type ProfileState } from "@/lib/profile";

/** The names of the controls, for `problemsFrom` (a 422 on one of them is shown next to it). */
export const PROFILE_CONTROLS: readonly string[] = PROFILE_FIELDS;

/**
 * The seven optional address / identifier boxes. Plain text: the only hint is an example, and any
 * judgement (is the code well-formed? too long?) belongs to the backend, whose answer appears
 * next to the box.
 */
export function ProfileFields({
  state,
  onChange,
  errors,
  disabled = false,
}: {
  state: ProfileState;
  onChange: (field: ProfileField, value: string) => void;
  errors: FieldErrors;
  disabled?: boolean;
}) {
  return (
    <>
      {PROFILE_FIELDS.map((field) => (
        <TextField
          key={field}
          label={PROFILE_LABELS[field]}
          name={field}
          value={state[field]}
          onChange={(value) => onChange(field, value)}
          error={errors[field]}
          disabled={disabled}
          autoComplete="off"
          hint={field === "country_code" ? "For example SE." : undefined}
        />
      ))}
    </>
  );
}
