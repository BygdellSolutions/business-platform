"use client";

import { useMemo, useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { EntityPicker, type PickerEntity } from "@/components/ui/EntityPicker";
import { customerSearch } from "@/features/customers/customer-picker";

/**
 * A customer filter for the horse list, inside its plain GET form: the hidden field of the
 * picker submits the chosen customer id under `name` (the backend filter name). Unlike a
 * form for a new assignment it also offers inactive customers, so the horses of a customer
 * that has been deactivated can still be found.
 */
export function CustomerFilter({ name, label, initial }: { name: string; label: string; initial: PickerEntity | null }) {
  const orgId = useOrgId();
  const [value, setValue] = useState<PickerEntity | null>(initial);
  const search = useMemo(() => customerSearch(orgId, { activeOnly: false }), [orgId]);

  return (
    <div className="w-56">
      <EntityPicker label={label} name={name} value={value} onChange={setValue} search={search} clearable placeholder="Any" />
    </div>
  );
}
