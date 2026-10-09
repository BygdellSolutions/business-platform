"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextAreaField } from "@/components/ui/Field";
import { RETURN_STATES } from "@/features/invoices/payment-labels";
import { apiFetch } from "@/lib/api/client";
import type { Invoice, InvoiceReturn } from "@/lib/api/types";
import { addDays } from "@/lib/dates";
import { trimQuantity } from "@/lib/decimal";
import { problemsFrom, useMutation } from "@/lib/forms";
import { formatTimestamp } from "@/lib/timestamps";

const DATE = "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900";
const CELL = "rounded border border-zinc-400 px-2 py-1 text-right dark:bg-zinc-900";
const ZERO = /^0*(?:\.0*)?$/;
const FOLLOW_UP_DAYS = 7;
const OPEN: InvoiceReturn["state"][] = ["requested", "goods_received", "approved"];
const EVENT_WORDS: Record<InvoiceReturn["events"][number]["kind"], string> = {
  opened: "Opened",
  note: "Note",
  goods_received: "Goods received",
  approved: "Approved",
  rejected: "Rejected",
  credited: "Credited",
  follow_up: "Follow-up moved",
};

/**
 * Return cases of an issued invoice, so a return asked for by email is not forgotten: open one with the lines and
 * quantities, a reason and a follow-up date; then goods received (ticked lines go back into stock), approved (the credit
 * form below opens with the case's lines and its credit note closes the case) or rejected with a reason. Every step and
 * note is in the case's log. The backend enforces the order of the steps and the quantities.
 */
export function ReturnsPanel({ invoice, canHandle, today, timeZone }: { invoice: Invoice; canHandle: boolean; today: string; timeZone: string | null }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [opening, setOpening] = useState(false);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [followUp, setFollowUp] = useState(addDays(today, FOLLOW_UP_DAYS));
  const problems = problemsFrom(error, ["reason", "follow_up_on"]);

  if (invoice.status !== "issued") return null;
  if (invoice.returns.length === 0 && !canHandle) return null;
  const returnable = invoice.lines.filter((line) => line.creditable_quantity !== null && !ZERO.test(line.creditable_quantity));

  async function open(event: FormEvent) {
    event.preventDefault();
    const lines = returnable
      .filter((line) => (quantities[line.id] ?? "").trim() !== "")
      .map((line) => ({ invoice_line_id: line.id, quantity: (quantities[line.id] ?? "").trim() }));
    const saved = await run(() => apiFetch<Invoice>(orgId, `/invoices/${invoice.id}/returns`, { method: "POST", body: { reason: reason.trim(), follow_up_on: followUp, lines } }));
    if (saved === null) return;
    setOpening(false);
    setQuantities({});
    setReason("");
    router.refresh();
  }

  return (
    <section aria-label="Returns" data-testid="returns" className="flex max-w-4xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Returns</h2>
      {invoice.returns.length === 0 && <p className="text-sm text-zinc-500">No return cases. Open one when a customer asks to send something back, so it is followed up.</p>}
      {invoice.returns.map((item) => (
        <ReturnCase key={`${item.id}-${item.events.length}`} invoice={invoice} item={item} canHandle={canHandle} timeZone={timeZone} today={today} />
      ))}

      {canHandle && returnable.length > 0 && !opening && (
        <div>
          <Button type="button" onClick={() => setOpening(true)} data-testid="open-return">
            Open a return…
          </Button>
        </div>
      )}
      {canHandle && opening && (
        <form onSubmit={open} noValidate aria-label="Open a return" className="flex flex-col gap-3 rounded border border-zinc-300 p-3 dark:border-zinc-700">
          <h3 className="font-medium">New return</h3>
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Line</th>
                <th className="py-1 pr-4">Unit</th>
                <th className="py-1 pr-4 text-right">Not credited</th>
                <th className="py-1 pr-4 text-right">To return</th>
              </tr>
            </thead>
            <tbody>
              {returnable.map((line) => (
                <tr key={line.id} className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4">{line.description}</td>
                  <td className="py-1 pr-4">{line.unit}</td>
                  <td className="py-1 pr-4 text-right">{trimQuantity(line.creditable_quantity ?? "")}</td>
                  <td className="py-1 pr-4 text-right">
                    <input
                      type="text"
                      inputMode="decimal"
                      aria-label={`Quantity to return of ${line.description}`}
                      value={quantities[line.id] ?? ""}
                      onChange={(event) => setQuantities((current) => ({ ...current, [line.id]: event.target.value }))}
                      className={`${CELL} w-28`}
                      data-testid="return-quantity"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <TextAreaField label="Reason" name="reason" value={reason} onChange={setReason} error={problems.byField.reason} required rows={2} />
          <label className="flex flex-col gap-1 text-sm font-medium">
            Follow up on
            <input type="date" name="follow_up_on" value={followUp} onChange={(event) => setFollowUp(event.target.value)} className={DATE} />
          </label>
          <ErrorSummary messages={[...problems.general, ...(problems.byField.follow_up_on ?? [])]} />
          <div className="flex gap-3">
            <Button type="submit" disabled={pending} data-testid="create-return">
              {pending ? "Opening…" : "Open return"}
            </Button>
            <Button type="button" onClick={() => setOpening(false)} disabled={pending} className="bg-zinc-200 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
              Cancel
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}

function ReturnCase({ invoice, item, canHandle, timeZone, today }: { invoice: Invoice; item: InvoiceReturn; canHandle: boolean; timeZone: string | null; today: string }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [rejection, setRejection] = useState("");
  const [toStock, setToStock] = useState<string[]>([]);
  const [followUp, setFollowUp] = useState(item.follow_up_on);
  const [showLog, setShowLog] = useState(false);
  const problems = problemsFrom(error, []);
  const isOpen = OPEN.includes(item.state);
  const due = isOpen && item.follow_up_on <= today; // ISO dates compare as text
  const base = `/invoices/${invoice.id}/returns/${item.id}`;
  const stockable = new Set(invoice.lines.filter((line) => line.stock_returnable !== null && !ZERO.test(line.stock_returnable)).map((line) => line.id));

  async function step(path: string, body: object, after?: () => void) {
    const saved = await run(() => apiFetch<Invoice>(orgId, `${base}${path}`, { method: path === "" ? "PATCH" : "POST", body }));
    if (saved === null) return;
    after?.();
    router.refresh();
  }

  function approve() {
    void step("/approve", {}, () => router.push(`?credit_return=${item.id}#credit-notes`));
  }

  return (
    <article data-testid="return-case" data-state={item.state} className="flex flex-col gap-2 rounded border border-zinc-300 p-3 text-sm dark:border-zinc-700">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold" data-testid="return-state">
          {RETURN_STATES[item.state]}
        </span>
        <span>· {item.reason}</span>
        {isOpen && (
          <span className={due ? "font-semibold text-orange-700 dark:text-orange-300" : "text-zinc-500"} data-testid="return-follow-up">
            · follow up {item.follow_up_on}
            {due && " (due)"}
          </span>
        )}
      </div>
      {item.rejection_reason && <p data-testid="return-rejection">Rejected: {item.rejection_reason}</p>}
      <ul className="list-disc pl-5">
        {item.lines.map((line) => (
          <li key={line.invoice_line_id}>
            {line.description}: {trimQuantity(line.quantity)}
            {line.unit ? ` (${line.unit})` : ""}
            {line.returned_to_stock && <span className="text-xs text-zinc-500"> · back in stock</span>}
            {canHandle && item.state === "requested" && stockable.has(line.invoice_line_id) && (
              <label className="ml-2 inline-flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={toStock.includes(line.invoice_line_id)}
                  onChange={(event) =>
                    setToStock((current) => (event.target.checked ? [...current, line.invoice_line_id] : current.filter((id) => id !== line.invoice_line_id)))
                  }
                  data-testid="return-to-stock"
                />
                back into stock when received
              </label>
            )}
          </li>
        ))}
      </ul>

      {canHandle && isOpen && (
        <div className="flex flex-wrap items-end gap-2">
          {item.state === "requested" && (
            <Button type="button" disabled={pending} onClick={() => void step("/goods-received", { to_stock: toStock })} data-testid="return-received">
              Goods received
            </Button>
          )}
          {item.state !== "approved" && (
            <Button type="button" disabled={pending} onClick={approve} data-testid="return-approve">
              Approve (credit)
            </Button>
          )}
          {item.state === "approved" && (
            <Button type="button" disabled={pending} onClick={() => router.push(`?credit_return=${item.id}#credit-notes`)} data-testid="return-credit">
              Create the credit note
            </Button>
          )}
          <Button type="button" disabled={pending} onClick={() => setRejecting(true)} data-testid="return-reject" className="bg-zinc-200 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
            Reject…
          </Button>
          <label className="flex flex-col gap-1 text-xs">
            Follow up on
            <input type="date" value={followUp} onChange={(event) => setFollowUp(event.target.value)} className={DATE} data-testid="return-follow-up-date" />
          </label>
          {followUp !== item.follow_up_on && (
            <Button type="button" disabled={pending} onClick={() => void step("", { follow_up_on: followUp })} data-testid="return-follow-up-save">
              Save date
            </Button>
          )}
        </div>
      )}
      {canHandle && rejecting && isOpen && (
        <div className="flex flex-col gap-2">
          <TextAreaField label="Why is the return rejected?" name="rejection" value={rejection} onChange={setRejection} rows={2} required />
          <div>
            <Button type="button" disabled={pending} onClick={() => void step("/reject", { reason: rejection.trim() }, () => setRejecting(false))} data-testid="return-reject-confirm">
              Reject return
            </Button>
          </div>
        </div>
      )}
      {canHandle && (
        <div className="flex flex-col gap-2">
          <TextAreaField label="Add a note" name={`note-${item.id}`} value={note} onChange={setNote} rows={2} />
          {note.trim() !== "" && (
            <div>
              <Button type="button" disabled={pending} onClick={() => void step("/notes", { note: note.trim() }, () => setNote(""))} data-testid="return-add-note">
                Add note
              </Button>
            </div>
          )}
        </div>
      )}
      <ErrorSummary messages={[...problems.general, ...Object.values(problems.byField).flat()]} />
      <div>
        <button type="button" className="text-xs underline" onClick={() => setShowLog((shown) => !shown)} data-testid="return-log-toggle">
          {showLog ? "Hide log" : `Show log (${item.events.length})`}
        </button>
        {showLog && (
          <ol className="mt-1 flex flex-col gap-1" data-testid="return-log">
            {item.events.map((event, index) => (
              <li key={index} className="text-xs">
                <span className="font-medium">{EVENT_WORDS[event.kind]}</span>
                {event.note && <>: {event.note}</>}
                <span className="text-zinc-500">
                  {" "}
                  · {formatTimestamp(event.created_at, timeZone)}
                  {event.created_by_name && ` by ${event.created_by_name}`}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </article>
  );
}
