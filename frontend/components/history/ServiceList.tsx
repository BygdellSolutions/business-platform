"use client";

import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import type { ServiceRecord } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/timestamps";
import { SortHeader } from "@/components/ui/SortHeader";
import type { SortValue } from "@/lib/table-sort";
import { useSortedRows } from "@/lib/use-sorted-rows";

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: ServiceRecord) => SortValue> = {
  when: (r) => ({ text: r.performed_at }),
  service: (r) => ({ text: r.description }),
  for: (r) => ({ text: r.subject_label }),
  by: (r) => ({ text: r.performed_by_name }),
  amount: (r) => ({ decimal: r.gross_amount }),
};

/**
 * Services performed (newest first), as FastAPI lists them for one record: what, when, for whom, by whom, the amount
 * and a link to the transaction. Amounts are the backend's strings; times are shown in the organization's zone.
 */
export function ServiceList({ orgId, services, timeZone, title }: { orgId: string; services: ServiceRecord[]; timeZone: string | null; title: string }) {
  const sorted = useSortedRows(services, SORT_COLUMNS);
  return (
    <CollapsibleSection title={title} count={services.length} testId="service-list" toggleTestId="services-toggle">
      {services.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-services">
          No services yet.
        </p>
      ) : (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <SortHeader label="When" {...sorted.header("when")} />
              <SortHeader label="Service" {...sorted.header("service")} />
              <SortHeader label="For" {...sorted.header("for")} />
              <SortHeader label="By" {...sorted.header("by")} />
              <SortHeader label="Amount" {...sorted.header("amount")} align="right" />
              <th className="py-1">Order</th>
            </tr>
          </thead>
          <tbody>
            {sorted.rows.map((service) => (
              <tr key={service.line_id} data-testid="service-row" className="border-b border-zinc-200 align-top dark:border-zinc-800">
                <td className="py-1 pr-4">{formatTimestamp(service.performed_at, timeZone)}</td>
                <td className="py-1 pr-4">
                  {service.description}
                  {service.notes && <span className="block text-xs italic text-zinc-500">{service.notes}</span>}
                </td>
                <td className="py-1 pr-4">{service.subject_label ?? <span className="text-zinc-500">no longer exists</span>}</td>
                <td className="py-1 pr-4">{service.performed_by_name ?? <span className="text-zinc-500">not recorded</span>}</td>
                <td className="py-1 pr-4 text-right">
                  <DecimalText value={service.gross_amount} /> {service.currency ?? ""}
                </td>
                <td className="py-1">
                  <Link href={`/o/${orgId}/transactions/${service.transaction_id}`} className="underline">
                    {service.transaction_date}
                  </Link>{" "}
                  <span className="text-xs text-zinc-500">{service.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </CollapsibleSection>
  );
}
