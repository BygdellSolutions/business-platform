import { SelectField, TextField } from "@/components/ui/Field";
import type { FieldErrors } from "@/lib/api/errors";
import type { DocumentLanguage, Organization, OrganizationUpdate, SellerTextField } from "@/lib/api/types";
import { blankToNull } from "@/lib/forms";

export const SELLER_TEXT_FIELDS: readonly SellerTextField[] = ["phone", "email", "website", "bankgiro", "plusgiro", "iban", "bic"];
export const SELLER_CONTROLS = [...SELLER_TEXT_FIELDS, "payment_terms_days", "approved_for_f_tax", "document_language"] as const;

export const SELLER_LABELS: Record<SellerTextField, string> = {
  phone: "Phone",
  email: "Email",
  website: "Website",
  bankgiro: "Bankgiro",
  plusgiro: "Plusgiro",
  iban: "IBAN",
  bic: "BIC",
};

const HINTS: Partial<Record<SellerTextField, string>> = {
  iban: "Spaces are fine; it is stored without them.",
  bic: "8 or 11 characters.",
};

export interface SellerState {
  text: Record<SellerTextField, string>;
  payment_terms_days: string;
  approved_for_f_tax: "" | "yes" | "no";
  document_language: "" | DocumentLanguage;
}

export function sellerState(organization: Organization): SellerState {
  return {
    text: Object.fromEntries(SELLER_TEXT_FIELDS.map((field) => [field, organization[field] ?? ""])) as Record<SellerTextField, string>,
    payment_terms_days: organization.payment_terms_days === null ? "" : String(organization.payment_terms_days),
    approved_for_f_tax: organization.approved_for_f_tax === null ? "" : organization.approved_for_f_tax ? "yes" : "no",
    document_language: organization.document_language ?? "",
  };
}

/** Only what changed, so a save never overwrites a field it did not touch. Days that are not a whole number go to the
 * backend as typed text, and its validation error is shown next to the field. */
export function sellerChanges(state: SellerState, record: Organization): OrganizationUpdate {
  const body: OrganizationUpdate = {};
  for (const field of SELLER_TEXT_FIELDS) {
    const value = blankToNull(state.text[field].trim());
    if (value !== record[field]) body[field] = value;
  }
  const days = state.payment_terms_days.trim();
  const daysValue = days === "" ? null : /^\d+$/.test(days) ? Number.parseInt(days, 10) : (days as unknown as number);
  if (daysValue !== record.payment_terms_days) body.payment_terms_days = daysValue;
  const fTax = state.approved_for_f_tax === "" ? null : state.approved_for_f_tax === "yes";
  if (fTax !== record.approved_for_f_tax) body.approved_for_f_tax = fTax;
  const language = state.document_language === "" ? null : state.document_language;
  if (language !== record.document_language) body.document_language = language;
  return body;
}

/** Contact and payment details, and the documents' language: what an invoice shows about the seller. */
export function SellerFields({ state, onChange, errors }: { state: SellerState; onChange: (state: SellerState) => void; errors: FieldErrors }) {
  const setText = (field: SellerTextField) => (value: string) => onChange({ ...state, text: { ...state.text, [field]: value } });
  return (
    <>
      <fieldset className="flex flex-col gap-4">
        <legend className="pb-1 text-sm font-medium">Contact and payment</legend>
        {SELLER_TEXT_FIELDS.map((field) => (
          <TextField key={field} label={SELLER_LABELS[field]} name={field} value={state.text[field]} onChange={setText(field)} error={errors[field]} hint={HINTS[field]} autoComplete="off" />
        ))}
        <TextField
          label="Payment terms (days)"
          name="payment_terms_days"
          value={state.payment_terms_days}
          onChange={(value) => onChange({ ...state, payment_terms_days: value })}
          error={errors.payment_terms_days}
          hint="An invoice created without a due date is due this many days after its invoice date."
          autoComplete="off"
        />
        <SelectField
          label="Approved for F-tax (F-skatt)"
          name="approved_for_f_tax"
          value={state.approved_for_f_tax}
          onChange={(value) => onChange({ ...state, approved_for_f_tax: value as SellerState["approved_for_f_tax"] })}
          options={[
            { value: "", label: "Not stated" },
            { value: "yes", label: "Yes" },
            { value: "no", label: "No" },
          ]}
          error={errors.approved_for_f_tax}
        />
      </fieldset>
      <fieldset className="flex flex-col gap-4">
        <legend className="pb-1 text-sm font-medium">Documents</legend>
        <SelectField
          label="Document language"
          name="document_language"
          value={state.document_language}
          onChange={(value) => onChange({ ...state, document_language: value as SellerState["document_language"] })}
          options={[
            { value: "", label: "Not set (English)" },
            { value: "sv", label: "Swedish" },
            { value: "en", label: "English" },
          ]}
          error={errors.document_language}
        />
      </fieldset>
    </>
  );
}

/** The same values for someone who may only read them. */
export function sellerRows(organization: Organization): [string, string, string | null][] {
  return [
    ...SELLER_TEXT_FIELDS.map((field): [string, string, string | null] => [field, SELLER_LABELS[field], organization[field]]),
    ["payment_terms_days", "Payment terms (days)", organization.payment_terms_days === null ? null : String(organization.payment_terms_days)],
    ["approved_for_f_tax", "Approved for F-tax", organization.approved_for_f_tax === null ? null : organization.approved_for_f_tax ? "Yes" : "No"],
    ["document_language", "Document language", organization.document_language === "sv" ? "Swedish" : organization.document_language === "en" ? "English" : null],
  ];
}
