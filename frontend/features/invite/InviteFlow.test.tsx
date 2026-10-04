import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InviteFlow } from "@/features/invite/InviteFlow";

const PRE = "P".repeat(43);
const TOKEN = "T".repeat(43);
const ORG = "00000000-0000-4000-8000-0000000000a1";

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;
let originalLocation: Location;
let replaceState: ReturnType<typeof vi.spyOn>;

const reply = (status: number, body: unknown = {}) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
const PREVIEW_NEW = { organization_name: "Fredrik Horse Therapy", email: "nina@invitees.test", role: "accountant", account_exists: false };
const PREVIEW_EXISTING = { ...PREVIEW_NEW, account_exists: true };

type Handler = (url: string, init: RequestInit) => Promise<Response>;
function route(handlers: Record<string, Handler>) {
  fetchMock.mockImplementation((url: string, init: RequestInit) => {
    if (url === "/api/auth/pre") return reply(200, { token: PRE });
    const handler = handlers[url];
    if (!handler) throw new Error(`unexpected request ${url}`);
    return handler(url, init);
  });
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  assign = vi.fn();
  originalLocation = window.location;
  Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, assign, hash: `#${TOKEN}`, pathname: "/invite", search: "", origin: "http://localhost:3000" } });
  replaceState = vi.spyOn(window.history, "replaceState");
  document.cookie = "bp_csrf=" + "C".repeat(43);
});
afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  vi.unstubAllGlobals();
  replaceState.mockRestore();
  document.cookie = "bp_csrf=; max-age=0";
  window.localStorage.clear();
  window.sessionStorage.clear();
});

const bodyOf = (call: [string, RequestInit]) => JSON.parse(String(call[1].body));
const callsTo = (url: string) => fetchMock.mock.calls.filter(([u]) => u === url) as [string, RequestInit][];

describe("the secret", () => {
  it("is removed from the address and the history at once, and put in no URL, header, storage or visible text", async () => {
    route({ "/api/invite/preview": () => reply(200, PREVIEW_NEW) });
    const { container } = render(<InviteFlow signedInEmail={null} />);

    await screen.findByTestId("invite-ready");

    expect(replaceState).toHaveBeenCalledWith(null, "", "/invite"); // no fragment left
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0);
    for (const [url, init] of fetchMock.mock.calls as [string, RequestInit][]) {
      expect(url).not.toContain(TOKEN);
      expect(JSON.stringify(init.headers ?? {})).not.toContain(TOKEN);
    }
    expect(bodyOf(callsTo("/api/invite/preview")[0])).toEqual({ token: TOKEN }); // only in a body
    expect(container.innerHTML).not.toContain(TOKEN);
    expect(document.cookie).not.toContain(TOKEN);
  });

  it("shows the generic state for no link, a malformed link and an unusable one, and asks nothing for the first two", async () => {
    window.location.hash = "";
    const { unmount } = render(<InviteFlow signedInEmail={null} />);
    expect(await screen.findByTestId("invite-invalid")).toBeInTheDocument();
    unmount();

    window.location.hash = "#not-a-token";
    const second = render(<InviteFlow signedInEmail={null} />);
    expect(await screen.findByTestId("invite-invalid")).toBeInTheDocument();
    second.unmount();
    expect(callsTo("/api/invite/preview")).toHaveLength(0);

    window.location.hash = `#${TOKEN}`;
    route({ "/api/invite/preview": () => reply(404, { detail: { code: "invitation_unusable" } }) });
    render(<InviteFlow signedInEmail={null} />);
    expect(await screen.findByTestId("invite-invalid")).toHaveTextContent("invalid or has expired");
  });
});

describe("a new person", () => {
  it("sees the invited email and role, cannot change the email, and creates the account with only token, name and password", async () => {
    route({ "/api/invite/accept-new": () => reply(200, { next: `/o/${ORG}` }), "/api/invite/preview": () => reply(200, PREVIEW_NEW) });
    render(<InviteFlow signedInEmail={null} />);

    expect(await screen.findByTestId("invite-organization")).toHaveTextContent("Fredrik Horse Therapy");
    expect(screen.getByTestId("invite-role")).toHaveTextContent("accountant");
    expect(screen.getByTestId("invite-email")).toHaveTextContent("nina@invitees.test");
    expect(screen.queryByLabelText(/email/i)).toBeNull(); // there is no email field to change

    await userEvent.type(screen.getByLabelText("Your name"), "Nina New");
    await userEvent.type(screen.getByLabelText("Password"), "a brand new long passphrase");
    await userEvent.type(screen.getByLabelText("Repeat the password"), "a brand new long passphrase");
    await userEvent.click(screen.getByTestId("invite-create"));

    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/o/${ORG}`));
    const call = callsTo("/api/invite/accept-new")[0];
    expect(bodyOf(call)).toEqual({ token: TOKEN, name: "Nina New", password: "a brand new long passphrase" });
    expect((call[1].headers as Record<string, string>)["x-pre-auth"]).toBe(PRE);
  });

  it("keeps the invitation after a weak password and shows the wording; mismatched passwords never reach the server", async () => {
    route({ "/api/invite/accept-new": () => reply(422, { detail: { code: "password_policy", message: "The password must be at least 12 characters." } }), "/api/invite/preview": () => reply(200, PREVIEW_NEW) });
    render(<InviteFlow signedInEmail={null} />);
    await screen.findByTestId("invite-create-form");

    await userEvent.type(screen.getByLabelText("Your name"), "Nina");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    await userEvent.type(screen.getByLabelText("Repeat the password"), "different");
    await userEvent.click(screen.getByTestId("invite-create"));
    expect(await screen.findByTestId("invite-error")).toHaveTextContent("do not match");
    expect(callsTo("/api/invite/accept-new")).toHaveLength(0);

    await userEvent.clear(screen.getByLabelText("Repeat the password"));
    await userEvent.type(screen.getByLabelText("Repeat the password"), "short");
    await userEvent.click(screen.getByTestId("invite-create"));
    expect(await screen.findByText("The password must be at least 12 characters.")).toBeInTheDocument();
    expect(screen.getByTestId("invite-create-form")).toBeInTheDocument(); // still there: retry with the same in-memory token
    expect(assign).not.toHaveBeenCalled();
  });

  it("turns into the sign-in form when the server says the account exists", async () => {
    route({ "/api/invite/accept-new": () => reply(409, { detail: { code: "account_exists" } }), "/api/invite/preview": () => reply(200, PREVIEW_NEW) });
    render(<InviteFlow signedInEmail={null} />);
    await screen.findByTestId("invite-create-form");
    await userEvent.type(screen.getByLabelText("Your name"), "Nina");
    await userEvent.type(screen.getByLabelText("Password"), "a brand new long passphrase");
    await userEvent.type(screen.getByLabelText("Repeat the password"), "a brand new long passphrase");
    await userEvent.click(screen.getByTestId("invite-create"));

    expect(await screen.findByTestId("invite-sign-in-form")).toBeInTheDocument();
  });
});

describe("an existing account", () => {
  it("signs in with the INVITED email through the protected login, then accepts with the same in-memory token (no redirect carries it)", async () => {
    route({
      "/api/invite/preview": () => reply(200, PREVIEW_EXISTING),
      "/api/auth/login": () => reply(200, { next: "/" }),
      "/api/invite/accept": () => reply(200, { organization_id: ORG, role: "accountant", joined: true }),
    });
    render(<InviteFlow signedInEmail={null} />);
    await screen.findByTestId("invite-sign-in-form");

    await userEvent.type(screen.getByLabelText("Password"), "correct horse battery");
    await userEvent.click(screen.getByTestId("invite-sign-in"));

    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/o/${ORG}`));
    expect(bodyOf(callsTo("/api/auth/login")[0])).toEqual({ email: "nina@invitees.test", password: "correct horse battery" }); // no `next`, no token
    const accepted = callsTo("/api/invite/accept")[0];
    expect(bodyOf(accepted)).toEqual({ token: TOKEN });
    expect((accepted[1].headers as Record<string, string>)["x-csrf-token"]).toBe("C".repeat(43));
    expect(assign.mock.calls.every(([target]) => !String(target).includes(TOKEN))).toBe(true);
  });

  it("a signed-in account with the invited email just joins", async () => {
    route({ "/api/invite/preview": () => reply(200, PREVIEW_EXISTING), "/api/invite/accept": () => reply(200, { organization_id: ORG, role: "accountant", joined: true }) });
    render(<InviteFlow signedInEmail="Nina@Invitees.test" />); // case differences do not matter

    await userEvent.click(await screen.findByTestId("invite-join"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/o/${ORG}`));
  });

  it("a signed-in WRONG account is told so, can sign out without losing the invitation, and then continues", async () => {
    route({
      "/api/invite/preview": () => reply(200, PREVIEW_EXISTING),
      "/api/auth/logout": () => reply(200, { revoked: "confirmed" }),
    });
    render(<InviteFlow signedInEmail="someone.else@invitees.test" />);

    expect(await screen.findByTestId("invite-wrong-account")).toHaveTextContent("someone.else@invitees.test");
    expect(screen.queryByTestId("invite-join")).toBeNull();
    expect(callsTo("/api/invite/accept")).toHaveLength(0); // nothing was tried, so nothing could be consumed

    await userEvent.click(screen.getByTestId("invite-sign-out"));

    expect(await screen.findByTestId("invite-sign-in-form")).toBeInTheDocument(); // same page, token still in memory
    expect(assign).not.toHaveBeenCalled();
    expect(callsTo("/api/invite/preview")).toHaveLength(1); // not previewed again: the token was never lost
  });

  it("when the server refuses a wrong account it says so and does not navigate", async () => {
    route({ "/api/invite/preview": () => reply(200, PREVIEW_EXISTING), "/api/invite/accept": () => reply(403, { detail: { code: "invitation_wrong_account" } }) });
    render(<InviteFlow signedInEmail="nina@invitees.test" />);
    await userEvent.click(await screen.findByTestId("invite-join"));

    expect(await screen.findByTestId("invite-error")).toHaveTextContent("different account");
    expect(assign).not.toHaveBeenCalled();
  });

  it("an invitation that turned unusable settles into the generic state", async () => {
    route({ "/api/invite/preview": () => reply(200, PREVIEW_EXISTING), "/api/invite/accept": () => reply(404, { detail: { code: "invitation_unusable" } }) });
    render(<InviteFlow signedInEmail="nina@invitees.test" />);
    await userEvent.click(await screen.findByTestId("invite-join"));
    expect(await screen.findByTestId("invite-invalid")).toBeInTheDocument();
  });
});
