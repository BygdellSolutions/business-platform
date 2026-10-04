import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LeaveOrganization } from "@/components/shell/LeaveOrganization";
import { OrgScope } from "@/components/shell/org-context";
import { MembersAdmin } from "@/features/members/MembersAdmin";
import type { Member, Role } from "@/lib/api/types";
import { ALL_ROLES, offered } from "@/lib/members";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;
let originalLocation: Location;

const reply = (status: number, body: unknown = undefined) =>
  Promise.resolve(new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  refresh.mockReset();
  assign = vi.fn();
  originalLocation = window.location;
  Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, assign } });
});
afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  vi.unstubAllGlobals();
});

const member = (name: string, role: Role, is_you = false): Member => ({ id: `id-${name}`, name, email: `${name.toLowerCase()}@example.test`, role, is_you });
const TEAM = [member("Olivia", "owner", true), member("Oskar", "owner"), member("Alma", "admin"), member("Anders", "admin"), member("Cecilia", "accountant"), member("Erik", "employee"), member("Vera", "viewer")];

function mount(actor: string, role: Role) {
  const list = TEAM.map((m) => ({ ...m, is_you: m.name === actor }));
  return render(
    <OrgScope orgId={ORG}>
      <MembersAdmin members={list} actorRole={role} />
    </OrgScope>,
  );
}

const row = (name: string) => screen.getAllByTestId("member-row").find((r) => r.getAttribute("data-email") === `${name.toLowerCase()}@example.test`)!;

describe("what is offered (presentation only; the backend decides)", () => {
  it("owner: any role and removal for others, step-down (not removal) for themselves only while another owner is listed", () => {
    expect(offered("owner", member("X", "admin"), 2)).toEqual({ roles: ALL_ROLES, remove: true });
    expect(offered("owner", member("Me", "owner", true), 2)).toEqual({ roles: ["admin", "accountant", "employee", "viewer"], remove: false });
    expect(offered("owner", member("Me", "owner", true), 1)).toEqual({ roles: [], remove: false });
  });

  it("admin: only accountants, employees and viewers, and only to those roles; nothing for owners, admins or themselves", () => {
    for (const target of ["accountant", "employee", "viewer"] as Role[]) expect(offered("admin", member("X", target), 1)).toEqual({ roles: ["accountant", "employee", "viewer"], remove: true });
    for (const target of ["owner", "admin"] as Role[]) expect(offered("admin", member("X", target), 1)).toEqual({ roles: [], remove: false });
    expect(offered("admin", member("Me", "admin", true), 1)).toEqual({ roles: [], remove: false });
  });

  it("other roles are offered nothing", () => {
    for (const actor of ["accountant", "employee", "viewer"] as Role[]) expect(offered(actor, member("X", "viewer"), 1)).toEqual({ roles: [], remove: false });
  });
});

describe("MembersAdmin", () => {
  it("shows name, email and role for everyone, and marks the current person", () => {
    mount("Olivia", "owner");
    expect(screen.getAllByTestId("member-row")).toHaveLength(TEAM.length);
    expect(within(row("Olivia")).getByText("(you)")).toBeInTheDocument();
    expect(within(row("Vera")).getByText("vera@example.test")).toBeInTheDocument();
  });

  it("an admin gets controls only on accountants, employees and viewers, never on owners, admins or themselves", () => {
    mount("Alma", "admin");
    for (const name of ["Olivia", "Oskar", "Alma", "Anders"]) {
      expect(within(row(name)).queryByTestId("member-role")).toBeNull();
      expect(within(row(name)).queryByTestId("member-remove")).toBeNull();
    }
    for (const name of ["Cecilia", "Erik", "Vera"]) {
      expect(within(row(name)).getByTestId("member-role")).toBeInTheDocument();
      expect(within(row(name)).getByTestId("member-remove")).toBeInTheDocument();
    }
    expect(within(row("Erik")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Employee", "Accountant", "Viewer"]);
  });

  it("an owner may remove and re-role others, and has no removal control on their own row", () => {
    mount("Olivia", "owner");
    expect(within(row("Alma")).getByTestId("member-remove")).toBeInTheDocument();
    expect(within(row("Olivia")).queryByTestId("member-remove")).toBeNull();
    expect(within(row("Olivia")).getByTestId("member-role")).toBeInTheDocument(); // step-down: another owner is listed
  });

  it("changing a role sends only the desired role, then re-reads the server's list", async () => {
    fetchMock.mockImplementation(() => reply(200, { ...TEAM[5], role: "viewer" }));
    mount("Alma", "admin");

    await userEvent.selectOptions(within(row("Erik")).getByTestId("member-role"), "viewer");

    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/o/${ORG}/members/id-Erik`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ role: "viewer" });
    expect(await screen.findByTestId("members-message")).toHaveTextContent("Erik is now viewer.");
  });

  it("removal asks first, then deletes, then re-reads", async () => {
    fetchMock.mockImplementation(() => reply(204));
    mount("Olivia", "owner");

    await userEvent.click(within(row("Vera")).getByTestId("member-remove"));
    expect(fetchMock).not.toHaveBeenCalled(); // the first click only asks
    await userEvent.click(screen.getByRole("button", { name: "Remove member" }));

    await waitFor(() => expect(refresh).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/o/${ORG}/members/id-Vera`);
    expect(init.method).toBe("DELETE");
    expect(await screen.findByTestId("members-message")).toHaveTextContent("Vera was removed");
  });

  it.each([
    [403, { detail: { code: "insufficient_authority", message: "x" } }, /not allowed to do that/],
    [404, { detail: "Member not found" }, /no longer exists/],
    [409, { detail: { code: "last_owner", message: "x" } }, /at least one owner/],
  ])("a stale screen refused with %s shows a safe message, claims no success and refreshes", async (status, body, text) => {
    fetchMock.mockImplementation(() => reply(status, body));
    mount("Olivia", "owner");

    await userEvent.selectOptions(within(row("Oskar")).getByTestId("member-role"), "viewer");

    const message = await screen.findByTestId("members-message");
    expect(message).toHaveTextContent(text);
    expect(message).not.toHaveTextContent(/is now/);
    expect(message).toHaveAttribute("role", "alert");
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  });

  it("a network failure is not shown as a success either", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new TypeError("down")));
    mount("Olivia", "owner");
    await userEvent.selectOptions(within(row("Oskar")).getByTestId("member-role"), "viewer");
    expect(await screen.findByTestId("members-message")).toHaveTextContent(/Could not reach the server/);
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });
});

describe("LeaveOrganization", () => {
  it("is separate from administration, asks first, and goes back to the organization selection on success", async () => {
    fetchMock.mockImplementation(() => reply(204));
    render(<LeaveOrganization orgId={ORG} />);

    await userEvent.click(screen.getByTestId("leave-organization"));
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/o/${ORG}/members/leave`);
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("stays put and explains when the server refuses because this is the last owner", async () => {
    fetchMock.mockImplementation(() => reply(409, { detail: { code: "last_owner", message: "x" } }));
    render(<LeaveOrganization orgId={ORG} />);
    await userEvent.click(screen.getByTestId("leave-organization"));
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));

    expect(await screen.findByTestId("leave-message")).toHaveTextContent(/last owner/);
    expect(assign).not.toHaveBeenCalled();
  });

  it("treats an already-gone membership like a completed departure", async () => {
    fetchMock.mockImplementation(() => reply(404, { detail: "Organization not found" }));
    render(<LeaveOrganization orgId={ORG} />);
    await userEvent.click(screen.getByTestId("leave-organization"));
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
  });
});
