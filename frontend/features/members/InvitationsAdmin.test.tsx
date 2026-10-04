import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { InvitationsAdmin, canManageInvitation, invitableRoles } from "@/features/members/InvitationsAdmin";
import type { Invitation, Role } from "@/lib/api/types";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const TOKEN = "T".repeat(43);
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

let fetchMock: ReturnType<typeof vi.fn>;
const reply = (status: number, body: unknown = undefined) =>
  Promise.resolve(new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  refresh.mockReset();
});
afterEach(() => vi.unstubAllGlobals());

const invitation = (email: string, role: Role, state: "pending" | "expired" = "pending"): Invitation => ({ id: `id-${email.split("@")[0]}`, email, role, created_at: "2026-10-04T10:00:00Z", expires_at: "2026-10-11T10:00:00Z", state });
const LIST = [invitation("viewer@x.test", "viewer"), invitation("admin@x.test", "admin"), invitation("old@x.test", "employee", "expired")];

function mount(role: Role, invitations: Invitation[] = LIST) {
  return render(
    <OrgScope orgId={ORG}>
      <InvitationsAdmin invitations={invitations} actorRole={role} />
    </OrgScope>,
  );
}
const row = (email: string) => screen.getAllByTestId("invitation-row").find((r) => r.getAttribute("data-email") === email)!;

describe("what is offered (presentation only)", () => {
  it("owner: every role; admin: accountant, employee, viewer; others: none", () => {
    expect(invitableRoles("owner")).toEqual(["owner", "admin", "accountant", "employee", "viewer"]);
    expect(invitableRoles("admin")).toEqual(["accountant", "employee", "viewer"]);
    for (const role of ["accountant", "employee", "viewer"] as Role[]) expect(invitableRoles(role)).toEqual([]);
    expect(canManageInvitation("admin", "admin")).toBe(false);
    expect(canManageInvitation("admin", "owner")).toBe(false);
    expect(canManageInvitation("admin", "viewer")).toBe(true);
    expect(canManageInvitation("owner", "owner")).toBe(true);
  });

  it("the form offers an admin only the roles an admin may invite", () => {
    mount("admin");
    expect(within(screen.getByTestId("invite-role-select")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Accountant", "Employee", "Viewer"]);
  });

  it("an admin gets revoke and regenerate only on invitations for accountants, employees and viewers", () => {
    mount("admin");
    expect(within(row("viewer@x.test")).getByTestId("invitation-revoke")).toBeInTheDocument();
    expect(within(row("old@x.test")).getByTestId("invitation-regenerate")).toBeInTheDocument();
    expect(within(row("admin@x.test")).queryByTestId("invitation-revoke")).toBeNull();
    expect(within(row("admin@x.test")).queryByTestId("invitation-regenerate")).toBeNull();
  });

  it("lists email, role and expiry state and never a token", () => {
    const { container } = mount("owner");
    expect(within(row("old@x.test")).getByTestId("invitation-state")).toHaveTextContent("Expired");
    expect(container.innerHTML).not.toMatch(/token/i);
  });
});

describe("the link is shown once", () => {
  it("creating sends only email and role, then shows a fragment link with the one-time warning; dismissing removes it", async () => {
    fetchMock.mockImplementation(() => reply(201, { ...invitation("new@x.test", "viewer"), token: TOKEN }));
    mount("admin", []);

    await userEvent.type(screen.getByLabelText("Email"), "new@x.test");
    await userEvent.selectOptions(screen.getByTestId("invite-role-select"), "viewer");
    await userEvent.click(screen.getByTestId("invite-submit"));

    const input = (await screen.findByTestId("invitation-link")) as HTMLInputElement;
    expect(input.value).toBe(`${window.location.origin}/invite#${TOKEN}`);
    expect(screen.getByTestId("invitation-link-panel")).toHaveTextContent("shown only now and cannot be retrieved again");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/o/${ORG}/invitations`);
    expect(JSON.parse(String(init.body))).toEqual({ email: "new@x.test", role: "viewer" });
    await waitFor(() => expect(refresh).toHaveBeenCalled());

    await userEvent.click(screen.getByTestId("invitation-dismiss"));
    expect(screen.queryByTestId("invitation-link")).toBeNull();
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0); // never stored
  });

  it("regenerating shows a NEW link once", async () => {
    fetchMock.mockImplementation(() => reply(201, { ...invitation("viewer@x.test", "viewer"), token: "R".repeat(43) }));
    mount("owner");

    await userEvent.click(within(row("viewer@x.test")).getByTestId("invitation-regenerate"));

    expect(((await screen.findByTestId("invitation-link")) as HTMLInputElement).value).toBe(`${window.location.origin}/invite#${"R".repeat(43)}`);
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[0]).toBe(`/api/o/${ORG}/invitations/id-viewer/regenerate`);
  });

  it("revoking asks first, then deletes and refreshes", async () => {
    fetchMock.mockImplementation(() => reply(204));
    mount("owner");

    await userEvent.click(within(row("viewer@x.test")).getByTestId("invitation-revoke"));
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Revoke invitation" }));

    await waitFor(() => expect(refresh).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect([url, init.method]).toEqual([`/api/o/${ORG}/invitations/id-viewer`, "DELETE"]);
    expect(await screen.findByTestId("invitations-message")).toHaveTextContent("was revoked");
  });
});

describe("refusals", () => {
  it.each([
    [409, { detail: { code: "already_member", message: "That person already belongs to this organization." } }, /already belongs/],
    [409, { detail: { code: "invitation_pending", message: "There is already a pending invitation for that email. Regenerate it to replace it." } }, /pending invitation/],
    [403, { detail: { code: "insufficient_authority", message: "x" } }, /not allowed/],
  ])("%s is shown as a refusal, never as success, and the list refreshes", async (status, body, text) => {
    fetchMock.mockImplementation(() => reply(status, body));
    mount("owner", []);
    await userEvent.type(screen.getByLabelText("Email"), "someone@x.test");
    await userEvent.click(screen.getByTestId("invite-submit"));

    expect(await screen.findByTestId("invitations-message")).toHaveTextContent(text);
    expect(screen.queryByTestId("invitation-link-panel")).toBeNull();
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });
});
