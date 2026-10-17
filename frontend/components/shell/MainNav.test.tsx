import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MainNav } from "@/components/shell/MainNav";
import { NAV } from "@/components/shell/nav";

let pathname = "";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const ORG = "00000000-0000-4000-8000-0000000000a1";

function current() {
  return screen.getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page").map((link) => link.textContent);
}

describe("MainNav", () => {
  beforeEach(() => {
    pathname = `/o/${ORG}`;
  });

  it("marks only the dashboard on the organization's home page", () => {
    render(<MainNav orgId={ORG} items={NAV} />);
    expect(current()).toEqual(["Dashboard"]);
  });

  it("marks the section entry on its list and on a record below it", () => {
    pathname = `/o/${ORG}/customers/00000000-0000-4000-8000-0000000000c1`;
    render(<MainNav orgId={ORG} items={NAV} />);
    expect(current()).toEqual(["Customers"]);
  });

  it("groups entries under their section headings, in order", () => {
    render(<MainNav orgId={ORG} items={NAV} />);
    const sales = screen.getByText("Sales").parentElement!;
    expect([...sales.querySelectorAll("a")].map((link) => link.textContent)).toEqual(["Orders", "Invoices"]);
  });
});
