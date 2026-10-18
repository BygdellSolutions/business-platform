import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InvoiceEmailSettings } from "@/features/settings/InvoiceEmailSettings";
import type { Organization } from "@/lib/api/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/shell/org-context", () => ({ useOrgId: () => "00000000-0000-4000-8000-0000000000a1" }));

afterEach(() => vi.unstubAllGlobals());

const organization = (over: Partial<Organization> = {}) => ({ id: "o1", name: "Org", invoice_email_subject: null, invoice_email_message: null, ...over }) as Organization;

function answer(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("InvoiceEmailSettings", () => {
  it("saves only what changed and shows what the backend stored", async () => {
    const fetchMock = answer(200, organization({ invoice_email_message: "Hej {customer}!" }));
    render(<InvoiceEmailSettings organization={organization()} canEdit />);

    await userEvent.type(screen.getByLabelText("Message"), "Hej {{customer}!");
    await userEvent.click(screen.getByTestId("save-invoice-email"));

    await waitFor(() => expect(screen.getByTestId("invoice-email-saved")).toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/o/00000000-0000-4000-8000-0000000000a1/organization");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ invoice_email_message: "Hej {customer}!" }); // the subject was not touched
  });

  it("says when there is nothing to save", async () => {
    const fetchMock = answer(200, organization());
    render(<InvoiceEmailSettings organization={organization({ invoice_email_subject: "S" })} canEdit />);
    await userEvent.click(screen.getByTestId("save-invoice-email"));
    expect(screen.getByTestId("invoice-email-unchanged")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the backend's explanation next to the field when a word is not known", async () => {
    answer(422, { detail: [{ loc: ["body", "invoice_email_subject"], msg: "Value error, These are not known: {custmer}. You can use {customer}, {invoice_number}.", type: "value_error" }] });
    render(<InvoiceEmailSettings organization={organization()} canEdit />);
    await userEvent.type(screen.getByLabelText("Subject"), "Hej {{custmer}");
    await userEvent.click(screen.getByTestId("save-invoice-email"));
    await waitFor(() => expect(screen.getByText(/These are not known: \{custmer\}/)).toBeInTheDocument());
    expect(screen.getByLabelText("Subject")).toHaveValue("Hej {custmer}"); // what was typed is kept
  });

  it("lists the words that can be used, and shows only the text to someone who cannot edit", () => {
    const { rerender } = render(<InvoiceEmailSettings organization={organization({ invoice_email_message: "Hej {customer}" })} canEdit />);
    expect(screen.getByTestId("placeholder-help")).toHaveTextContent("{invoice_number}");
    rerender(<InvoiceEmailSettings organization={organization({ invoice_email_message: "Hej {customer}" })} canEdit={false} />);
    expect(screen.queryByTestId("save-invoice-email")).toBeNull();
    expect(screen.getByTestId("setting-invoice_email_message")).toHaveTextContent("Hej {customer}");
    expect(screen.getByTestId("setting-invoice_email_subject")).toHaveTextContent("Not set");
  });
});
