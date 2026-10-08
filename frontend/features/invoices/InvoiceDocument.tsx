import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { DiscountSteps } from "@/components/ui/DiscountSteps";
import { FieldSnapshots } from "@/components/snapshots/FieldSnapshots";
import { InvoiceStatusBadge } from "@/features/invoices/InvoiceStatusBadge";
import type { Invoice, PartySnapshot } from "@/lib/api/types";

/**
 * The invoice as a DOCUMENT: every word and every amount on it is the invoice's own stored
 * content. Nothing here fetches or looks anything up, and nothing is calculated: amounts, totals
 * and the VAT breakdown are the strings the server stored.
 *
 * The one place a live record is mentioned is the "Source transactions" section: links for
 * navigation and audit, visibly secondary, labelled with the date the invoice itself recorded.
 * Following one shows the live record, which may by now differ from what this invoice says.
 */
export function InvoiceDocument({ invoice, orgId }: { invoice: Invoice; orgId: string }) {
  const heading = invoice.number_text !== null ? `Invoice ${invoice.number_text}` : "Draft invoice (no number yet)";
  return (
    <article aria-label="Invoice" data-testid="invoice-document" data-status={invoice.status} className="flex flex-col gap-5">
      <header className="flex flex-wrap items-center gap-3">
        <h2 data-testid="invoice-heading" className="text-xl font-semibold">
          {heading}
        </h2>
        {invoice.number_text !== null && (
          <span data-testid="invoice-number" className="rounded border border-zinc-400 px-2 py-0.5 text-sm font-medium">
            {invoice.number_text}
          </span>
        )}
        <InvoiceStatusBadge status={invoice.status} />
        <span data-testid="invoice-currency" className="text-sm text-zinc-600 dark:text-zinc-400">
          {invoice.currency}
        </span>
      </header>

      <dl className="grid max-w-md grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm" aria-label="Invoice details">
        <dt>Invoice date</dt>
        <dd data-testid="invoice-date">{invoice.invoice_date}</dd>
        <dt>Due date</dt>
        <dd data-testid="invoice-due-date">{invoice.due_date ?? "—"}</dd>
        <dt>Description</dt>
        <dd data-testid="invoice-description">{invoice.description ?? "—"}</dd>
        {invoice.issued_at !== null && (
          <>
            <dt>Issued</dt>
            <dd data-testid="invoice-issued-at">{invoice.issued_at}</dd>
          </>
        )}
        <dt>Version</dt>
        <dd data-testid="invoice-version">{invoice.version}</dd>
      </dl>

      <div className="grid gap-6 sm:grid-cols-2">
        <Party title="From" testId="issuer" party={invoice.issuer_snapshot} />
        <Party title="Billed to" testId="customer" party={invoice.customer_snapshot} />
      </div>

      <section aria-label="Lines" className="flex flex-col gap-2">
        <h3 className="text-lg font-medium">Lines</h3>
        {invoice.lines.length === 0 ? (
          <p data-testid="no-lines">This invoice has no lines.</p>
        ) : (
          <div className="overflow-x-auto">
            <table data-testid="invoice-lines" className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-zinc-300 dark:border-zinc-700">
                  <th className="py-1 pr-3">#</th>
                  <th className="py-1 pr-3">Description</th>
                  <th className="py-1 pr-3">Unit</th>
                  <th className="py-1 pr-3 text-right">Quantity</th>
                  <th className="py-1 pr-3 text-right">Unit price</th>
                  <th className="py-1 pr-3 text-right">VAT %</th>
                  <th className="py-1 pr-3 text-right">Net</th>
                  <th className="py-1 pr-3 text-right">VAT</th>
                  <th className="py-1 text-right">Gross</th>
                </tr>
              </thead>
              <tbody>
                {invoice.lines.map((line) => (
                  <tr key={line.id} data-testid="invoice-line" className="border-b border-zinc-200 align-top dark:border-zinc-800">
                    <td className="py-1 pr-3">{line.position}</td>
                    <td className="py-1 pr-3">
                      <div data-testid="line-description">{line.description}</div>
                      <FieldSnapshots fields={line.fields} label={`Fields of line ${line.position}`} testId="line-fields" />
                    </td>
                    <td className="py-1 pr-3">{line.unit}</td>
                    <td className="py-1 pr-3 text-right" data-testid="line-quantity">
                      <DecimalText value={line.quantity} />
                    </td>
                    <td className="py-1 pr-3 text-right" data-testid="line-unit-price">
                      <DecimalText value={line.unit_price_ex_vat} />
                      <DiscountSteps list={line.list_unit_price} catalog={line.catalog_discount_percent} customer={line.customer_discount_percent} />
                    </td>
                    <td className="py-1 pr-3 text-right" data-testid="line-vat-rate">
                      <DecimalText value={line.vat_rate} />
                    </td>
                    <td className="py-1 pr-3 text-right" data-testid="line-net">
                      <DecimalText value={line.net_amount} />
                    </td>
                    <td className="py-1 pr-3 text-right" data-testid="line-vat">
                      <DecimalText value={line.vat_amount} />
                    </td>
                    <td className="py-1 text-right" data-testid="line-gross">
                      <DecimalText value={line.gross_amount} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-label="Totals" data-testid="invoice-totals" className="flex flex-col gap-2">
        <h3 className="text-lg font-medium">Totals ({invoice.currency})</h3>
        <dl className="grid max-w-md grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
          <dt>Net (excl. VAT)</dt>
          <dd className="text-right" data-testid="total-net">
            <DecimalText value={invoice.net_amount} />
          </dd>
          <dt>VAT</dt>
          <dd className="text-right" data-testid="total-vat">
            <DecimalText value={invoice.vat_amount} />
          </dd>
          <dt className="font-medium">Gross (incl. VAT)</dt>
          <dd className="text-right font-medium" data-testid="total-gross">
            <DecimalText value={invoice.gross_amount} />
          </dd>
        </dl>
        {invoice.vat_breakdown.length > 0 && (
          <table data-testid="vat-breakdown" className="max-w-md text-left text-sm">
            <caption className="pb-1 text-left text-zinc-500">VAT breakdown</caption>
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">VAT rate (%)</th>
                <th className="py-1 pr-4 text-right">Net</th>
                <th className="py-1 text-right">VAT</th>
              </tr>
            </thead>
            <tbody>
              {invoice.vat_breakdown.map((row) => (
                <tr key={row.vat_rate} data-testid="vat-row">
                  <td className="py-1 pr-4">
                    <DecimalText value={row.vat_rate} />
                  </td>
                  <td className="py-1 pr-4 text-right">
                    <DecimalText value={row.net_amount} />
                  </td>
                  <td className="py-1 text-right">
                    <DecimalText value={row.vat_amount} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section aria-label="Source transactions" data-testid="sources" className="flex flex-col gap-2 border-t border-zinc-300 pt-3 text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
        <h3 className="text-sm font-medium">Source transactions (navigation and audit only)</h3>
        <p>These links open the live records, which may have changed since this invoice recorded them. The invoice above is not affected by them.</p>
        <ul className="flex flex-col gap-2">
          {invoice.transactions.map((source) => (
            <li key={source.transaction_id} data-testid="source">
              <Link href={`/o/${orgId}/transactions/${source.transaction_id}`} data-testid="source-link" className="underline">
                Transaction of {source.transaction_date}
              </Link>
              <FieldSnapshots fields={source.fields} label={`Fields of the transaction of ${source.transaction_date}`} testId="transaction-fields" />
            </li>
          ))}
        </ul>
      </section>
    </article>
  );
}

function Party({ title, testId, party }: { title: string; testId: string; party: PartySnapshot }) {
  const name = party.legal_name ?? party.name;
  const cityLine = [party.postal_code, party.city].filter((part): part is string => !!part).join(" ");
  const lines = [party.address_line1, party.address_line2, cityLine, party.country_code].filter((part): part is string => !!part);
  return (
    <section aria-label={title} data-testid={`party-${testId}`} className="flex flex-col gap-0.5 text-sm">
      <h3 className="text-lg font-medium">{title}</h3>
      <div data-testid={`party-${testId}-name`} className="font-medium">
        {name}
      </div>
      {lines.map((line) => (
        <div key={line}>{line}</div>
      ))}
      {party.registration_number && <div>Registration no. {party.registration_number}</div>}
      {party.vat_number && <div>VAT no. {party.vat_number}</div>}
      {party.email && <div>{party.email}</div>}
      {party.phone && <div>{party.phone}</div>}
    </section>
  );
}
