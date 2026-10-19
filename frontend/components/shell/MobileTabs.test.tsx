import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MobileTabs } from "@/components/shell/MobileTabs";
import { NAV } from "@/components/shell/nav";

let pathname = "";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const ORG = "00000000-0000-4000-8000-0000000000a1";
const bar = () => within_bar();
function within_bar() {
  return [...screen.getByTestId("mobile-tabs").querySelectorAll(":scope > ul > li > a")].map((link) => link.textContent);
}

describe("MobileTabs", () => {
  it("puts the everyday pages in the bar and the rest under More", async () => {
    pathname = `/o/${ORG}`;
    render(<MobileTabs orgId={ORG} items={NAV} />);
    expect(bar()).toEqual(["Dashboard", "Scheduling", "Orders", "Customers"]);
    expect(screen.queryByRole("link", { name: "Invoices" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("link", { name: "Invoices" })).toHaveAttribute("href", `/o/${ORG}/invoices`);
  });

  it("marks the current page, and More when the page is under it", () => {
    pathname = `/o/${ORG}/customers/abc`;
    render(<MobileTabs orgId={ORG} items={NAV} />);
    expect(screen.getByRole("link", { name: "Customers" })).toHaveAttribute("aria-current", "page");
  });

  it("does not offer a page the role may not see", async () => {
    pathname = `/o/${ORG}`;
    render(<MobileTabs orgId={ORG} items={NAV.filter((item) => item.roles === undefined)} />);
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.queryByRole("link", { name: "Members" })).toBeNull();
  });
});
