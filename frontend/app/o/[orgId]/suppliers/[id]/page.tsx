import Link from "next/link";

import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { Notice } from "@/components/ui/Notice";
import { SupplierDetails } from "@/features/suppliers/SupplierDetails";
import { SupplierDialogs } from "@/features/suppliers/SupplierDialogs";
import { SupplierForm } from "@/features/suppliers/SupplierForm";
import { readActiveRole } from "@/lib/active-role";
import type { Incoming, Organization, Supplier } from "@/lib/api/types";
import { readRecordHistory } from "@/lib/history-server";
import { canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";
import { SECONDARY_LINK } from "@/components/ui/Button";

/** Another organization's supplier, a random id and a malformed id all end in the same generic not-found page. */
export default async function SupplierPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string; id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId, id } = await params;
  const { created } = await searchParams;
  const recordId = requireUuid(id);
  const [supplier, role, organization, history, deliveries] = await Promise.all(
    [
      serverRead<Supplier>(orgId, `/api/suppliers/${recordId}`),
      readActiveRole(orgId),
      serverRead<Organization>(orgId, "/api/organization"),
      readRecordHistory(orgId, "supplier", recordId),
      serverRead<Incoming[]>(
        orgId,
        "/api/inventory/incoming",
        `?${new URLSearchParams({ supplier_id: recordId, open_only: "false" })}`,
      ),
    ],
  );

  const dialogs = <SupplierDialogs orgId={orgId} deliveries={deliveries} timeZone={organization.timezone} />;

  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight" data-testid="record-name">
          {supplier.name}
        </h1>
        <Link href={`/o/${orgId}/suppliers`} className={SECONDARY_LINK}>
          Back to suppliers
        </Link>
      </div>
      <p className="-mt-3 text-sm text-zinc-500" data-testid="record-number">
        Supplier no. {supplier.number}
      </p>
      {created === "1" && <Notice testId="created">Supplier created.</Notice>}
      <RecordMeta
        record={supplier}
        people={history.history.people}
        timeZone={organization.timezone}
      />
      {canWriteRecords(role) ? (
        <SupplierForm key={supplier.id} supplier={supplier} actions={dialogs} />
      ) : (
        <>
          <div className="flex flex-wrap gap-2">{dialogs}</div>
          <SupplierDetails supplier={supplier} />
        </>
      )}
      <RecordHistory
        data={history}
        entityType="supplier"
        timeZone={organization.timezone}
      />
    </div>
  );
}
