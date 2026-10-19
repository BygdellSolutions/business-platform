import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { Membership } from "@/lib/api/types";
import { OrgScope, useOrgId } from "@/components/shell/org-context";
import { OrgMenu } from "@/components/shell/OrgMenu";
import { UserMenu, initialsOf } from "@/components/shell/UserMenu";

const A = "00000000-0000-4000-8000-0000000000a1";
const B = "00000000-0000-4000-8000-0000000000b2";
const ORGS: Membership[] = [
  { id: A, name: "Fredrik Horse Therapy", role: "owner" },
  { id: B, name: "Umeå Stable Services", role: "admin" },
];

/** A page with tenant-specific CLIENT state: something typed, and something loaded. */
function Stateful() {
  const orgId = useOrgId();
  const [typed, setTyped] = useState("");
  const [loadedFor] = useState(orgId); // like data fetched when the page mounted
  return (
    <div>
      <input aria-label="typed" value={typed} onChange={(event) => setTyped(event.target.value)} />
      <output aria-label="loaded-for">{loadedFor}</output>
      <output aria-label="current">{orgId}</output>
    </div>
  );
}

describe("OrgScope", () => {
  it("provides the organization to client components below it", () => {
    render(<OrgScope orgId={A}><Stateful /></OrgScope>);
    expect(screen.getByLabelText("current")).toHaveTextContent(A);
  });

  it("discards ALL client state when the organization changes, even without a reload", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<OrgScope orgId={A}><Stateful /></OrgScope>);
    await user.type(screen.getByLabelText("typed"), "Anna from A");
    expect(screen.getByLabelText("typed")).toHaveValue("Anna from A");

    rerender(<OrgScope orgId={B}><Stateful /></OrgScope>); // the organization changes under a mounted tree

    expect(screen.getByLabelText("typed")).toHaveValue(""); // typed text gone
    expect(screen.getByLabelText("loaded-for")).toHaveTextContent(B); // "loaded" data re-fetched for B, not kept from A
    expect(screen.getByLabelText("loaded-for")).not.toHaveTextContent(A);
  });

  it("keeps state while the organization stays the same", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<OrgScope orgId={A}><Stateful /></OrgScope>);
    await user.type(screen.getByLabelText("typed"), "keep");

    rerender(<OrgScope orgId={A}><Stateful /></OrgScope>);

    expect(screen.getByLabelText("typed")).toHaveValue("keep");
  });

  it("refuses to be used outside a scope, so tenant data cannot be fetched without one", () => {
    const quiet = () => {};
    const original = console.error;
    console.error = quiet;
    try {
      expect(() => render(<Stateful />)).toThrow(/OrgScope/);
    } finally {
      console.error = original;
    }
  });
});

const open = async () => userEvent.click(screen.getByTestId("org-switcher-toggle"));

describe("OrgMenu", () => {
  it("is a drop-down: it shows the current organization and lists the others only when opened", async () => {
    render(<OrgMenu organizations={ORGS} currentId={A} />);
    expect(screen.getByTestId("org-switcher-current")).toHaveTextContent("Fredrik Horse Therapy");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByTestId("org-switcher-toggle")).toHaveAttribute("aria-expanded", "false");
    await open();
    expect(screen.getByRole("menu", { name: "Organizations" })).toBeInTheDocument();
    expect(screen.getByTestId("org-switcher-toggle")).toHaveAttribute("aria-expanded", "true");
  });

  it("closes with Escape and when something else is clicked", async () => {
    render(<OrgMenu organizations={ORGS} currentId={A} />);
    await open();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    await open();
    await userEvent.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("shows how many organizations the account owns and may own, and no count when none is given", async () => {
    const { rerender } = render(<OrgMenu organizations={ORGS} currentId={A} created={{ count: 1, max: 1 }} />);
    await open();
    expect(screen.getByTestId("created-count")).toHaveTextContent("Created 1 / 1");
    expect(screen.queryByTestId("create-organization-link")).toBeNull();
    rerender(<OrgMenu organizations={ORGS} currentId={A} />);
    expect(screen.queryByTestId("created-count")).toBeNull();
  });

  it("marks the current organization and shows the others as links to their own URLs", async () => {
    render(<OrgMenu organizations={ORGS} currentId={A} />);
    await open();
    expect(screen.getByRole("menuitem", { name: /Fredrik Horse Therapy/ })).toHaveAttribute("aria-current", "true");
    expect(screen.queryByRole("link", { name: /Fredrik Horse Therapy/ })).toBeNull();
    expect(screen.getByRole("menuitem", { name: "Umeå Stable Services" })).toHaveAttribute("href", `/o/${B}`);
  });

  it("uses plain anchors so a switch is a full navigation (no client-side transition)", async () => {
    render(<OrgMenu organizations={ORGS} currentId={A} />);
    await open();
    const link = screen.getByRole("menuitem", { name: "Umeå Stable Services" });
    expect(link.tagName).toBe("A");
    expect([...link.attributes].map((a) => a.name).sort()).toEqual(["class", "href", "role"]); // no next/link prefetch or data attributes
  });

  it("offers the creation link only when told the account may create organizations (presentation; the backend decides)", async () => {
    const { rerender } = render(<OrgMenu organizations={ORGS} currentId={A} />);
    await open();
    expect(screen.queryByTestId("create-organization-link")).toBeNull();

    rerender(<OrgMenu organizations={ORGS} currentId={A} canCreate />);
    const link = screen.getByTestId("create-organization-link");
    expect(link).toHaveAttribute("href", "/organizations/new");
    expect(link.tagName).toBe("A");
  });

  it("lists every organization the user belongs to, once", async () => {
    render(<OrgMenu organizations={ORGS} currentId={B} />);
    await open();
    expect(screen.getAllByRole("menuitem")).toHaveLength(2);
    expect(screen.getByRole("menuitem", { name: "Fredrik Horse Therapy" })).toHaveAttribute("href", `/o/${A}`);
  });
});

describe("UserMenu", () => {
  it("is a round avatar with the person's initials; the menu is closed until it is pressed", async () => {
    render(<UserMenu name="Fredrik Axö" email="fredrik@example.test" mode="session" />);
    const bubble = screen.getByTestId("user-menu-toggle");
    expect(bubble).toHaveTextContent("FA");
    expect(bubble).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("menu", { hidden: true })).not.toBeVisible();
    await userEvent.click(bubble);
    expect(screen.getByRole("menu", { name: "Account" })).toBeVisible();
    expect(screen.getByTestId("user-email")).toHaveTextContent("fredrik@example.test");
    expect(screen.getByTestId("account-link")).toHaveAttribute("href", "/account");
    expect(screen.getByTestId("sign-out")).toHaveTextContent("Sign out");
  });

  it("closes with Escape, and without a sign-in mode offers no sign out", async () => {
    render(<UserMenu name="" email="maria@example.test" mode={null} />);
    expect(screen.getByTestId("user-menu-toggle")).toHaveTextContent("M"); // no name: the address gives the initial
    await userEvent.click(screen.getByTestId("user-menu-toggle"));
    expect(screen.queryByTestId("sign-out")).toBeNull();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByTestId("user-menu-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  it("makes initials from the first two words of a name, or from the address", () => {
    expect(initialsOf("Anna Maria Lindqvist", "a@x.se")).toBe("AM");
    expect(initialsOf("åsa", "a@x.se")).toBe("Å");
    expect(initialsOf("", "zed@x.se")).toBe("Z");
  });
});
