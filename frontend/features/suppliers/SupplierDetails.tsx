"use client";

import { DetailList } from "@/components/ui/DetailList";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { Supplier } from "@/lib/api/types";
import { PROFILE_FIELDS, PROFILE_LABELS } from "@/lib/profile";

/** A supplier for someone who may read it but not change it: no form, no buttons. */
export function SupplierDetails({ supplier }: { supplier: Supplier }) {
  return (
    <DetailList
      testId="record-details"
      details={[
        { label: "Name", value: supplier.name },
        { label: "Contact person", value: supplier.contact_person },
        { label: "Email", value: supplier.email },
        { label: "Phone", value: supplier.phone },
        { label: "Our customer number", value: supplier.our_customer_number },
        ...PROFILE_FIELDS.map((field) => ({ label: PROFILE_LABELS[field], value: supplier[field] })),
        { label: "Status", value: <StatusBadge active={supplier.active} /> },
      ]}
    />
  );
}
