import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import type { ServiceRecord } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/timestamps";

/**
 * Services performed (newest first), as FastAPI lists them for one record: what, when, for whom, by whom, the amount
 * and a link to the transaction. Amounts are the backend's strings; times are shown in the organization's zone.
 */
export function ServiceList({ orgId, services, timeZone, title }: { orgId: string; services: ServiceRecord[]; timeZone: string | null; title: string }) {
  return (
    <section aria-label={title} data-testid="service-list" className="flex max-w-4xl flex-col gap-2">
      <h2 className="text-lg font-semibold">{title}</h2>
      {services.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-services">
          No services yet.
        </p>
      ) : (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">When</th>
              <th className="py-1 pr-4">Service</th>
              <th className="py-1 pr-4">For</th>
              <th className="py-1 pr-4">By</th>
              <th className="py-1 pr-4 text-right">Amount</th>
              <th className="py-1">Order</th>
            </tr>
          </thead>
          <tbody>
            {services.map((service) => (
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
    </section>
  );
}
