import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "@/components/ui/PageHeader";

describe("PageHeader", () => {
  it("has one level-1 heading, the description and the action", () => {
    render(<PageHeader title="Customers" description="The people you sell to." actions={<a href="/new">New customer</a>} />);
    expect(screen.getByRole("heading", { level: 1, name: "Customers" })).toBeInTheDocument();
    expect(screen.getByText("The people you sell to.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New customer" })).toBeInTheDocument();
  });

  it("works without a description or an action (a viewer has no action)", () => {
    render(<PageHeader title="Invoices" actions={false} />);
    expect(screen.getByRole("heading", { name: "Invoices" })).toBeInTheDocument();
  });
});
