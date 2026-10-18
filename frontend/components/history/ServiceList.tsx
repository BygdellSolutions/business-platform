"use client";


import { DecimalText } from "@/components/ui/DecimalText";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import { OrderCells, OrderHeaders } from "@/components/history/OrderColumns";
import type { ServiceRecord } from "@/lib/api/types";
import { formatDay } from "@/lib/timestamps";
import { SortHeader } from "@/components/ui/SortHeader";
import type { SortValue } from "@/lib/table-sort";
import { useSortedRows } from "@/lib/use-sorted-rows";

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: ServiceRecord) => SortValue> = {
  number: (r) => ({ number: r.transaction_number }),
  date: (r) => ({ text: r.performed_at }),
  status: (r) => ({ text: r.status }),
  service: (r) => ({ text: r.description }),
  for: (r) => ({ text: r.subject_label }),
  by: (r) => ({ text: r.performed_by_name }),
  amount: (r) => ({ decimal: r.gross_amount }),
};

/**
 * Services performed (newest first), as FastAPI lists them for one record: what, when, for whom, by whom, the amount
 * and a link to the transaction. Amounts are the backend's strings; times are shown in the organization's zone.
 */
export function ServiceList({ orgId, services, timeZone, title, bare = false }: { orgId: string; services: ServiceRecord[]; timeZone: string | null; title: string; bare?: boolean }) {
  const sorted = useSortedRows(services, SORT_COLUMNS);
  const body = (
    <>
      {services.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-services">
          No services yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <OrderHeaders header={sorted.header} />
                <SortHeader label="Service" {...sorted.header("service")} />
                <SortHeader label="For" {...sorted.header("for")} />
                <SortHeader label="By" {...sorted.header("by")} />
                <SortHeader label="Amount" {...sorted.header("amount")} align="right" last />
              </tr>
            </thead>
            <tbody>
              {sorted.rows.map((service) => (
                <tr key={service.line_id} data-testid="service-row" className="border-b border-zinc-200 align-top dark:border-zinc-800">
                  <OrderCells orgId={orgId} transactionId={service.transaction_id} number={service.transaction_number} date={formatDay(service.performed_at, timeZone)} status={service.status} />
                  <td className="py-1 pr-4">
                    {service.description}
                    {service.notes && <span className="block text-xs italic text-zinc-500">{service.notes}</span>}
                  </td>
                  <td className="py-1 pr-4">{service.subject_label ?? <span className="text-zinc-500">no longer exists</span>}</td>
                  <td className="py-1 pr-4">{service.performed_by_name ?? <span className="text-zinc-500">not recorded</span>}</td>
                  <td className="py-1 text-right">
                    <DecimalText value={service.gross_amount} /> {service.currency ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
  // In a pop-up window the window has the title and the frame already.
  if (bare) return <div data-testid="service-list" className="flex flex-col gap-2">{body}</div>;
  return (
    <CollapsibleSection title={title} count={services.length} testId="service-list" toggleTestId="services-toggle">
      {body}
    </CollapsibleSection>
  );
}
