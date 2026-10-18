import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { InvoiceStatusBadge } from "@/features/invoices/InvoiceStatusBadge";

describe("the Sent badge", () => {
  it("shows next to Unpaid and Partially paid once the invoice was emailed", () => {
    const { rerender } = render(<InvoiceStatusBadge status="issued" paymentStatus="unpaid" emailed />);
    expect(screen.getByTestId("invoice-status")).toHaveTextContent("Unpaid");
    expect(screen.getByTestId("invoice-sent")).toHaveTextContent("Sent");
    rerender(<InvoiceStatusBadge status="issued" paymentStatus="partially_paid" emailed />);
    expect(screen.getByTestId("invoice-status")).toHaveTextContent("Partially paid");
    expect(screen.getByTestId("invoice-sent")).toBeInTheDocument();
  });

  it("is not there for an invoice that was not emailed", () => {
    render(<InvoiceStatusBadge status="issued" paymentStatus="unpaid" emailed={false} />);
    expect(screen.queryByTestId("invoice-sent")).toBeNull();
    expect(screen.getByTestId("invoice-status")).toHaveTextContent("Unpaid");
  });

  it("is dropped as soon as the invoice is paid: only Paid is said", () => {
    render(<InvoiceStatusBadge status="issued" paymentStatus="paid" emailed />);
    expect(screen.getByTestId("invoice-status")).toHaveTextContent("Paid");
    expect(screen.queryByTestId("invoice-sent")).toBeNull();
  });

  it("is dropped for a fully credited invoice, and never shown on a draft", () => {
    const { rerender } = render(<InvoiceStatusBadge status="issued" paymentStatus="unpaid" creditStatus="credited" emailed />);
    expect(screen.getByTestId("invoice-status")).toHaveTextContent("Credited");
    expect(screen.queryByTestId("invoice-sent")).toBeNull();
    rerender(<InvoiceStatusBadge status="draft" emailed />);
    expect(screen.getByTestId("invoice-status")).toHaveTextContent("Draft");
    expect(screen.queryByTestId("invoice-sent")).toBeNull();
  });
});

describe("the Sent badge has a look of its own", () => {
  it("is not the colour of any other badge an invoice can carry, so it is never mistaken for Partly credited", () => {
    render(<InvoiceStatusBadge status="issued" paymentStatus="partially_paid" creditStatus="partly_credited" emailed openReturns={1} />);
    const toneOf = (testId: string) => /tone-[a-z]+/.exec(screen.getByTestId(testId).className)?.[0];
    const tones = [toneOf("invoice-status"), toneOf("invoice-sent"), toneOf("credit-status"), toneOf("return-open") ?? "tone-orange"];
    expect(toneOf("invoice-sent")).toBe("tone-teal");
    expect(new Set(tones).size).toBe(tones.length); // four badges, four different tones
  });

  it("is defined in the stylesheet, light and dark", () => {
    const css = readFileSync("app/globals.css", "utf8");
    expect(css).toMatch(/@utility tone-teal\s*\{/);
    expect(css).toMatch(/--teal-text:/g);
    expect((css.match(/--teal-text:/g) ?? []).length).toBe(2); // one for light, one for dark
  });
});
