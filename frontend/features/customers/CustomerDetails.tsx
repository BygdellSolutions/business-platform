"use client";

import { DetailList } from "@/components/ui/DetailList";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { Customer } from "@/lib/api/types";
import { PROFILE_FIELDS, PROFILE_LABELS } from "@/lib/profile";

const TYPE_LABELS = { person: "Person", company: "Company" } as const;

/** A customer for someone who may read it but not change it: no form, no buttons. */
export function CustomerDetails({ customer }: { customer: Customer }) {
  return (
    <DetailList
      testId="record-details"
      details={[
        { label: "Type", value: TYPE_LABELS[customer.customer_type] },
        { label: "Name", value: customer.name },
        { label: "Email", value: customer.email },
        { label: "Phone", value: customer.phone },
        { label: "Default discount %", value: customer.default_discount_percent },
        ...PROFILE_FIELDS.map((field) => ({ label: PROFILE_LABELS[field], value: customer[field] })),
        { label: "Status", value: <StatusBadge active={customer.active} /> },
      ]}
    />
  );
}
