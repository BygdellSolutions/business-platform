import type { Profile, ProfileField } from "@/lib/api/types";
import { blankToNull } from "@/lib/forms";
import { PROFILE_FIELDS, PROFILE_LABELS } from "@/lib/profile-labels";

// The labels live in a module without hooks, so server components can use them too.
export { PROFILE_FIELDS, PROFILE_LABELS };

export const EMPTY_PROFILE: Profile = {
  address_line1: null,
  address_line2: null,
  postal_code: null,
  city: null,
  country_code: null,
  registration_number: null,
  vat_number: null,
};

/** What the text boxes show: null becomes "". */
export type ProfileState = Record<ProfileField, string>;

export function profileState(profile: Partial<Profile> = {}): ProfileState {
  return Object.fromEntries(PROFILE_FIELDS.map((field) => [field, profile[field] ?? ""])) as ProfileState;
}

/** The whole profile as a request body: a blank box is null ("not set"), anything else is sent as typed. */
export function profileBody(state: ProfileState): Profile {
  return Object.fromEntries(PROFILE_FIELDS.map((field) => [field, blankToNull(state[field])])) as unknown as Profile;
}

/** Only the profile fields that differ from the saved record, so an edit never overwrites what it did not touch. */
export function profileChanges(state: ProfileState, saved: Partial<Profile>): Partial<Profile> {
  const changes: Partial<Profile> = {};
  for (const field of PROFILE_FIELDS) {
    const next = blankToNull(state[field]);
    if (next !== (saved[field] ?? null)) changes[field] = next;
  }
  return changes;
}
