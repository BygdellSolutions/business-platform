import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { Membership } from "@/lib/api/types";
import { OrgScope, useOrgId } from "@/components/shell/org-context";
import { OrgSwitcher } from "@/components/shell/OrgSwitcher";

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

describe("OrgSwitcher", () => {
  it("shows how many organizations the account owns and may own, and no count when none is given", () => {
    const { rerender } = render(<OrgSwitcher organizations={ORGS} currentId={A} owned={{ count: 1, max: 1 }} />);
    expect(screen.getByTestId("owned-count")).toHaveTextContent("Owned 1 / 1");
    expect(screen.queryByTestId("create-organization-link")).toBeNull();
    rerender(<OrgSwitcher organizations={ORGS} currentId={A} />);
    expect(screen.queryByTestId("owned-count")).toBeNull();
  });

  it("shows the current organization as text and the others as links to their own URLs", () => {
    render(<OrgSwitcher organizations={ORGS} currentId={A} />);

    expect(screen.getByText("Fredrik Horse Therapy")).toHaveAttribute("aria-current", "true");
    expect(screen.queryByRole("link", { name: "Fredrik Horse Therapy" })).toBeNull();
    expect(screen.getByRole("link", { name: "Umeå Stable Services" })).toHaveAttribute("href", `/o/${B}`);
  });

  it("uses plain anchors so a switch is a full navigation (no client-side transition)", () => {
    render(<OrgSwitcher organizations={ORGS} currentId={A} />);

    const link = screen.getByRole("link", { name: "Umeå Stable Services" });
    expect(link.tagName).toBe("A");
    expect([...link.attributes].map((a) => a.name).sort()).toEqual(["class", "href"]); // no next/link prefetch or data attributes
  });

  it("offers the creation link only when told the account may create organizations (presentation; the backend decides)", () => {
    const { rerender } = render(<OrgSwitcher organizations={ORGS} currentId={A} />);
    expect(screen.queryByTestId("create-organization-link")).toBeNull();

    rerender(<OrgSwitcher organizations={ORGS} currentId={A} canCreate />);
    const link = screen.getByTestId("create-organization-link");
    expect(link).toHaveAttribute("href", "/organizations/new");
    expect(link.tagName).toBe("A");
  });

  it("lists every organization the user belongs to, once", () => {
    render(<OrgSwitcher organizations={ORGS} currentId={B} />);

    expect(screen.getAllByText(/Fredrik Horse Therapy|Umeå Stable Services/)).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Fredrik Horse Therapy" })).toHaveAttribute("href", `/o/${A}`);
  });
});
