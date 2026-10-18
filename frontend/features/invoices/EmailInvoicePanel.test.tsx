import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmailInvoicePanel, failureText } from "@/features/invoices/EmailInvoicePanel";
import type { InvoiceEmailDraft, InvoiceEmailLog } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/shell/org-context", () => ({ useOrgId: () => "00000000-0000-4000-8000-0000000000a1" }));

afterEach(() => vi.unstubAllGlobals());

const draft = (over: Partial<InvoiceEmailDraft> = {}): InvoiceEmailDraft => ({ to: "kund@example.test", subject: "Faktura 1001 från Org A", message: "", ...over });
const log = (over: Partial<InvoiceEmailLog> = {}): InvoiceEmailLog => ({ id: "e1", to_email: "kund@example.test", subject: "s", note: null, status: "sent", error: null, created_at: "2026-10-18T10:00:00Z", sent_by_name: "Tina", ...over });

function answer(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("EmailInvoicePanel", () => {
  it("fills in the customer's address and sends the invoice with the message", async () => {
    const fetchMock = answer(201, log({ status: "sent" }));
    render(<EmailInvoicePanel invoiceId="inv1" draft={draft()} canSend emails={[]} timeZone={null} />);

    expect(screen.getByLabelText("Send to")).toHaveValue("kund@example.test");
    await userEvent.type(screen.getByLabelText("Message (optional)"), "Tack!");
    expect(screen.getByLabelText("Subject")).toHaveValue("Faktura 1001 från Org A");
    await userEvent.click(screen.getByTestId("send-invoice-email"));

    await waitFor(() => expect(screen.getByTestId("email-sent")).toHaveTextContent("Invoice sent to kund@example.test."));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/o/00000000-0000-4000-8000-0000000000a1/invoices/inv1/email");
    expect(JSON.parse(String(init.body))).toEqual({ to: "kund@example.test", subject: "Faktura 1001 från Org A", note: "Tack!" });
  });

  it("says plainly when it did not go, and never shows the provider's words", async () => {
    answer(201, log({ status: "failed", error: "unauthorized" }));
    render(<EmailInvoicePanel invoiceId="inv1" draft={draft()} canSend emails={[]} timeZone={null} />);
    await userEvent.click(screen.getByTestId("send-invoice-email"));
    await waitFor(() => expect(screen.getByTestId("email-failed")).toHaveTextContent("Email sending is not working right now"));
    expect(screen.queryByTestId("email-sent")).toBeNull();
  });

  it("asks for an address when the customer has none, and cannot send without one", async () => {
    render(<EmailInvoicePanel invoiceId="inv1" draft={draft({ to: "" })} canSend emails={[]} timeZone={null} />);
    expect(screen.getByTestId("no-customer-email")).toBeInTheDocument();
    expect(screen.getByTestId("send-invoice-email")).toBeDisabled();
  });

  it("starts from the pre-made text, sends what was changed, and goes back to the pre-made text for the next one", async () => {
    const fetchMock = answer(201, log({ status: "sent" }));
    render(<EmailInvoicePanel invoiceId="inv1" draft={draft({ subject: "Rubrik", message: "Hej Anna, tack!" })} canSend emails={[]} timeZone={null} />);
    const box = screen.getByLabelText("Message (optional)");
    expect(box).toHaveValue("Hej Anna, tack!");

    await userEvent.type(box, " Ring mig.");
    await userEvent.click(screen.getByTestId("send-invoice-email"));

    await waitFor(() => expect(screen.getByTestId("email-sent")).toBeInTheDocument());
    expect(JSON.parse(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body)).note).toBe("Hej Anna, tack! Ring mig.");
    expect(box).toHaveValue("Hej Anna, tack!");
  });

  it("shows the list to a reader but no form", () => {
    render(<EmailInvoicePanel invoiceId="inv1" draft={draft()} canSend={false} emails={[log(), log({ id: "e2", status: "failed", error: "rejected" })]} timeZone={null} />);
    expect(screen.queryByTestId("send-invoice-email")).toBeNull();
    expect(screen.getAllByTestId("invoice-email")).toHaveLength(2);
    expect(screen.getByText("Only owners, admins and accountants can email invoices.")).toBeInTheDocument();
  });

  it("explains each failure in plain words", () => {
    expect(failureText("rejected")).toContain("address was refused");
    expect(failureText("unavailable")).toContain("Try again in a moment");
    expect(failureText("not_configured")).toContain("not working right now");
    expect(failureText(null)).toContain("not working right now");
  });
});
