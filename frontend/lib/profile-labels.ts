import type { ProfileField } from "@/lib/api/types";

/**
 * The optional address and business identifiers shared by a customer and the organization.
 * They are free text: whether an address is complete or a number "looks right" is not decided
 * here, nor (beyond the shape of a country code) by the backend.
 */
export const PROFILE_LABELS: Record<ProfileField, string> = {
  address_line1: "Address line 1",
  address_line2: "Address line 2",
  postal_code: "Postal code",
  city: "City",
  country_code: "Country code",
  registration_number: "Registration number",
  vat_number: "VAT number",
};

export const PROFILE_FIELDS = Object.keys(PROFILE_LABELS) as ProfileField[];
