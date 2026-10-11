import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TransactionStatusBadge } from "@/features/transactions/TransactionStatusBadge";

describe("TransactionStatusBadge", () => {
  const label = (props: Parameters<typeof TransactionStatusBadge>[0]) => {
    const { unmount } = render(<TransactionStatusBadge {...props} />);
    const text = screen.getByTestId("tx-status").textContent;
    unmount();
    return text;
  };

  it("reads a completed order by where its invoicing stands, and everything else by its own status", () => {
    expect(label({ status: "draft", invoice: "none" })).toBe("Draft");
    expect(label({ status: "cancelled", invoice: "none" })).toBe("Cancelled");
    expect(label({ status: "completed", paid: true, invoice: "none" })).toBe("Paid");
    expect(label({ status: "completed", invoice: "none" })).toBe("Not invoiced");
    expect(label({ status: "completed", invoice: "draft" })).toBe("Invoice draft");
    expect(label({ status: "completed", invoice: "invoiced" })).toBe("Invoiced");
    expect(label({ status: "completed" })).toBe("Completed"); // where invoicing is not known (the order page)
  });
});
