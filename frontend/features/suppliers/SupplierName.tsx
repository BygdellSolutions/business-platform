import Link from "next/link";

import type { SupplierRef } from "@/lib/api/types";

/** A supplier named by a delivery: a link to it, marked when it has been deactivated since. */
export function SupplierName({ orgId, supplier }: { orgId: string; supplier: SupplierRef | null }) {
  if (supplier === null) return null;
  return (
    <>
      <Link href={`/o/${orgId}/suppliers/${supplier.id}`} className="underline" data-testid="supplier-link">
        {supplier.name}
      </Link>
      {!supplier.active && <span className="text-xs text-zinc-500"> (inactive)</span>}
    </>
  );
}
