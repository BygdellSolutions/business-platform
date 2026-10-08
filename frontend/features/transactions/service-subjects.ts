import type { PickerSearch } from "@/components/ui/EntityPicker";
import { customerSearch } from "@/features/customers/customer-picker";
import { horseSearch } from "@/features/horses/horse-picker";

/**
 * Who or what a service can be performed for, as the Add line form offers it. The key is the backend's registry
 * key (FastAPI checks that the record exists in this organization, is a service subject and is active); a module
 * that adds a new kind of subject adds one entry here.
 */
export interface SubjectKind {
  type: string;
  label: string;
  search: (orgId: string) => PickerSearch;
}

export const SUBJECT_KINDS: readonly SubjectKind[] = [
  { type: "horse", label: "Horse", search: horseSearch },
  { type: "customer", label: "Person (customer)", search: (orgId) => customerSearch(orgId, { activeOnly: true }) },
];
