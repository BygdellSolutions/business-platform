import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { DangerZone } from "@/features/settings/DangerZone";
import type { Member, Role } from "@/lib/api/types";

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
const sent = (index = 0) => {
  const [url, init] = fetchMock.mock.calls[index] as [string, RequestInit];
  return { url, body: JSON.parse(String(init.body)) as Record<string, unknown> };
};

function mount(role: Role, members: Member[] = []) {
  return render(
    <OrgScope orgId={ORG}>
      <DangerZone organizationName="Umeå Häst & Rehab" role={role} members={members} passwordChecked />
    </OrgScope>,
  );
}

describe("leaving", () => {
  it("sends the password and goes back to the organization selection", async () => {
    fetchMock.mockImplementation(() => reply(204));
    mount("employee");
    await userEvent.type(screen.getByLabelText("Your password"), "secret");
    await userEvent.click(screen.getByTestId("leave-organization"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    expect(sent()).toEqual({ url: `/api/o/${ORG}/members/leave`, body: { password: "secret" } });
  });

  it("shows the backend's refusal of a wrong password and stays", async () => {
    fetchMock.mockImplementation(() => reply(403, { detail: { code: "reauthentication_failed", message: "Confirm with your password: it was missing or not correct." } }));
    mount("employee");
    await userEvent.click(screen.getByTestId("leave-organization"));
    expect(await screen.findByTestId("leave-error")).toHaveTextContent(/password/);
    expect(assign).not.toHaveBeenCalled();
  });

  it("treats an already-gone membership like a completed departure", async () => {
    fetchMock.mockImplementation(() => reply(404, { detail: "Organization not found" }));
    mount("employee");
    await userEvent.click(screen.getByTestId("leave-organization"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
  });

  it("tells the sole owner to transfer or delete instead, and offers no leave", () => {
    mount("owner", [member("Olle", "owner", true), member("Erik", "employee")]);
    expect(screen.getByTestId("sole-owner")).toHaveTextContent(
      "You are the only owner of this organization. Transfer ownership to another member before leaving, or delete the organization.",
    );
    expect(screen.queryByTestId("leave-organization")).toBeNull();
  });

  it("lets one of two owners leave", () => {
    mount("owner", [member("Olle", "owner", true), member("Oskar", "owner")]);
    expect(screen.getByTestId("leave-organization")).toBeEnabled();
  });
});

describe("transfer and delete are for owners only", () => {
  it.each<Role>(["admin", "accountant", "employee", "viewer"])("%s sees only Leave", (role) => {
    mount(role, [member("Olle", "owner"), member("Me", role, true)]);
    expect(screen.getByTestId("leave-organization")).toBeInTheDocument();
    expect(screen.queryByTestId("transfer-ownership")).toBeNull();
    expect(screen.queryByTestId("delete-organization")).toBeNull();
  });
});

describe("transferring ownership", () => {
  it("offers the other non-owner members and sends the membership id with the password", async () => {
    fetchMock.mockImplementation(() => reply(204));
    mount("owner", [member("Olle", "owner", true), member("Oskar", "owner"), member("Erik", "employee"), member("Vera", "viewer")]);
    const select = screen.getByLabelText("New owner");
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Choose a member",
      "Erik (erik@example.test, employee)",
      "Vera (vera@example.test, viewer)",
    ]);
    await userEvent.selectOptions(select, "id-Erik");
    await userEvent.type(within(screen.getByRole("form", { name: "Transfer ownership" })).getByLabelText("Your password"), "secret");
    await userEvent.click(screen.getByTestId("transfer-ownership"));
    expect(await screen.findByTestId("transferred")).toBeInTheDocument();
    expect(sent()).toEqual({ url: `/api/o/${ORG}/organization/transfer-ownership`, body: { membership_id: "id-Erik", password: "secret" } });
    expect(refresh).toHaveBeenCalled();
  });
});

describe("after the transfer the page refreshes as an admin", () => {
  it("keeps the confirmation although the transfer section is gone", async () => {
    fetchMock.mockImplementation(() => reply(204));
    const members = [member("Olle", "owner", true), member("Erik", "employee")];
    const { rerender } = mount("owner", members);
    await userEvent.selectOptions(screen.getByLabelText("New owner"), "id-Erik");
    await userEvent.click(screen.getByTestId("transfer-ownership"));
    expect(await screen.findByTestId("transferred")).toBeInTheDocument();

    // What router.refresh() brings: the same component, now for an admin (the server says so).
    rerender(
      <OrgScope orgId={ORG}>
        <DangerZone organizationName="Umeå Häst & Rehab" role="admin" members={[member("Olle", "admin", true), member("Erik", "owner")]} passwordChecked />
      </OrgScope>,
    );
    expect(screen.queryByTestId("transfer-ownership")).toBeNull();
    expect(screen.getByTestId("transferred")).toBeInTheDocument();
  });
});

describe("deleting the organization", () => {
  it("warns that everything is deleted for good, including issued invoices, and mentions the retention rules", () => {
    mount("owner", [member("Olle", "owner", true)]);
    const warning = screen.getByTestId("delete-warning");
    expect(warning).toHaveTextContent(/permanently deletes the organization and ALL of its data/);
    expect(warning).toHaveTextContent(/issued invoices and their PDFs/);
    expect(warning).toHaveTextContent(/bokföringslagen/);
  });

  it("is enabled only after the name is typed and the consequence is acknowledged, then sends both", async () => {
    fetchMock.mockImplementation(() => reply(204));
    mount("owner", [member("Olle", "owner", true)]);
    const button = screen.getByTestId("delete-organization");
    expect(button).toBeDisabled();
    const form = screen.getByRole("form", { name: "Delete organization" });
    await userEvent.type(within(form).getByLabelText(/Type the organization/), "Umeå Häst & Rehab");
    expect(button).toBeDisabled();
    await userEvent.click(within(form).getByRole("checkbox"));
    await userEvent.type(within(form).getByLabelText("Your password"), "secret");
    await userEvent.click(button);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    expect(sent()).toEqual({ url: `/api/o/${ORG}/organization/delete`, body: { confirm_name: "Umeå Häst & Rehab", password: "secret" } });
  });

  it("shows a name mismatch from the backend and stays", async () => {
    fetchMock.mockImplementation(() => reply(422, { detail: [{ loc: ["body", "confirm_name"], msg: "x", type: "confirmation_mismatch" }] }));
    mount("owner", [member("Olle", "owner", true)]);
    const form = screen.getByRole("form", { name: "Delete organization" });
    await userEvent.type(within(form).getByLabelText(/Type the organization/), "Umeå");
    await userEvent.click(within(form).getByRole("checkbox"));
    await userEvent.click(screen.getByTestId("delete-organization"));
    expect(await screen.findByTestId("delete-error")).toHaveTextContent(/exactly as shown/);
    expect(assign).not.toHaveBeenCalled();
  });
});
