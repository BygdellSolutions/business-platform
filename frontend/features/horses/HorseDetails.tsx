import Link from "next/link";

import { DetailList } from "@/components/ui/DetailList";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { CustomerRef, Horse } from "@/lib/api/types";

const SEX_LABELS = { mare: "Mare", stallion: "Stallion", gelding: "Gelding" } as const;

function CustomerLink({ orgId, customer }: { orgId: string; customer: CustomerRef }) {
  return (
    <>
      <Link href={`/o/${orgId}/customers/${customer.id}`} className="underline">
        {customer.name}
      </Link>
      {!customer.active && <span className="ml-1 text-xs text-zinc-500">(inactive)</span>}
    </>
  );
}

/** A horse for someone who may read it but not change it: no form, no pickers. */
export function HorseDetails({ orgId, horse }: { orgId: string; horse: Horse }) {
  return (
    <DetailList
      testId="record-details"
      details={[
        { label: "Name", value: horse.name },
        { label: "Owner", value: <CustomerLink orgId={orgId} customer={horse.owner} /> },
        { label: "Stable", value: horse.stable === null ? null : <CustomerLink orgId={orgId} customer={horse.stable} /> },
        { label: "Birth year", value: horse.birth_year === null ? null : String(horse.birth_year) },
        { label: "Sex", value: horse.sex === null ? null : SEX_LABELS[horse.sex] },
        { label: "Breed", value: horse.breed },
        { label: "Status", value: <StatusBadge active={horse.active} /> },
      ]}
    />
  );
}
