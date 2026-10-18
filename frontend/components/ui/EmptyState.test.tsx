import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@/components/ui/EmptyState";

describe("EmptyState", () => {
  it("keeps the empty test id on the title alone, with the hint and action beside it", () => {
    render(<EmptyState title="No customers yet." hint="Add one." action={<a href="/new">Add</a>} />);
    expect(screen.getByTestId("empty")).toHaveTextContent(/^No customers yet\.$/);
    expect(screen.getByText("Add one.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add" })).toBeInTheDocument();
  });

  it("shows no action when none is given", () => {
    render(<EmptyState title="No invoices match." />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
