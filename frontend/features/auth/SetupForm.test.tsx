import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetupForm } from "@/features/auth/SetupForm";

const PRE = "P".repeat(43);
const LINK = "L".repeat(43);
const PASSWORD = "a brand new long passphrase";

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;
let replaceState: ReturnType<typeof vi.spyOn>;

const reply = (status: number, body: unknown = {}) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

function arrange(setupResponse: () => Promise<Response> = () => reply(200, { next: "/" })) {
  fetchMock.mockImplementation((url: string) => (url === "/api/auth/pre" ? reply(200, { token: PRE }) : setupResponse()));
}

/** Land on the page the way the operator's link does: the secret in the URL fragment. */
function openLink(hash: string = `#${LINK}`) {
  window.history.pushState({}, "", `/setup${hash}`);
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  assign = vi.fn();
  replaceState = vi.spyOn(window.history, "replaceState");
});
afterEach(() => {
  replaceState.mockRestore();
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

function withAssign() {
  Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, assign, hash: window.location.hash } });
}

async function fill(password = PASSWORD, confirm = PASSWORD) {
  await waitFor(() => expect(screen.getByTestId("setup-submit")).toBeEnabled());
  await userEvent.type(screen.getByLabelText("New password"), password);
  await userEvent.type(screen.getByLabelText("Repeat the password"), confirm);
  await userEvent.click(screen.getByTestId("setup-submit"));
}

const setupCall = () => fetchMock.mock.calls.find(([url]) => url === "/api/auth/setup") as [string, RequestInit & { headers: Record<string, string> }] | undefined;

describe("the link secret", () => {
  it("is removed from the address bar and the history at once", async () => {
    arrange();
    openLink();

    render(<SetupForm />);

    await waitFor(() => expect(screen.getByTestId("setup-form")).toBeInTheDocument());
    expect(replaceState).toHaveBeenCalledTimes(1);
    expect(replaceState).toHaveBeenCalledWith(null, "", "/setup");
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain(LINK);
  });

  it("is removed exactly once even when React runs the effect twice", async () => {
    arrange();
    openLink();

    render(
      <StrictMode>
        <SetupForm />
      </StrictMode>,
    );

    await waitFor(() => expect(screen.getByTestId("setup-form")).toBeInTheDocument());
    expect(replaceState).toHaveBeenCalledTimes(1);
  });

  it("is sent only in the body of the POST, never in a URL or a header, and is never shown", async () => {
    arrange();
    openLink();
    render(<SetupForm />);

    await fill();

    const [url, init] = setupCall()!;
    expect(url).toBe("/api/auth/setup");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ token: LINK, password: PASSWORD });
    expect(init.headers).toEqual({ "content-type": "application/json", "x-pre-auth": PRE });
    for (const [calledUrl] of fetchMock.mock.calls) expect(String(calledUrl)).not.toContain(LINK);
    expect(document.body.innerHTML).not.toContain(LINK);
    expect(window.location.href).not.toContain(LINK);
  });

  it("never touches browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    arrange();
    openLink();
    render(<SetupForm />);

    await fill();

    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it.each(["", "#", "#short", `#${"L".repeat(44)}`, `#${"L".repeat(42)}!`, "#../../etc"])("a missing or malformed link (%j) shows one generic message and no form", async (hash) => {
    arrange();
    openLink(hash);

    render(<SetupForm />);

    expect(await screen.findByTestId("setup-invalid")).toHaveTextContent("This link is invalid or has expired.");
    expect(screen.queryByTestId("setup-form")).toBeNull();
    if (hash.replace("#", "") !== "") expect(replaceState).toHaveBeenCalled(); // whatever secret-looking text was there is gone from the address too
  });
});

describe("a second link opened in the same tab", () => {
  it("is taken from the fragment too (a hash-only navigation does not reload the page), and removed at once", async () => {
    arrange();
    openLink("#" + "X".repeat(43));
    render(<SetupForm />);
    await waitFor(() => expect(screen.getByTestId("setup-form")).toBeInTheDocument());

    const second = "Y".repeat(43);
    window.history.pushState({}, "", `/setup#${second}`);
    window.dispatchEvent(new HashChangeEvent("hashchange"));

    await waitFor(() => expect(replaceState).toHaveBeenCalledTimes(2));
    expect(window.location.hash).toBe("");
    await fill();
    expect(JSON.parse(setupCall()![1].body as string).token).toBe(second);
  });

  it("an invalid one replaces a valid one with the generic message", async () => {
    arrange();
    openLink();
    render(<SetupForm />);
    await waitFor(() => expect(screen.getByTestId("setup-form")).toBeInTheDocument());

    window.history.pushState({}, "", "/setup#short");
    window.dispatchEvent(new HashChangeEvent("hashchange"));

    expect(await screen.findByTestId("setup-invalid")).toBeInTheDocument();
  });
});

describe("SetupForm", () => {
  it("checks the repeated password locally and sends nothing when it differs", async () => {
    arrange();
    openLink();
    render(<SetupForm />);

    await fill(PASSWORD, PASSWORD + "x");

    expect((await screen.findByTestId("setup-error")).textContent).toBe("The two passwords do not match.");
    expect(setupCall()).toBeUndefined();
  });

  it("signs in and goes to / on success, dropping the link", async () => {
    arrange();
    openLink();
    withAssign();
    render(<SetupForm />);

    await fill();

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
  });

  it("answers a used, expired or invalid link with the generic message and removes the form (the link cannot be retried)", async () => {
    arrange(() => reply(400, { detail: { code: "invalid_setup_link", message: "This link is invalid or has expired." } }));
    openLink();
    render(<SetupForm />);

    await fill();

    expect(await screen.findByTestId("setup-invalid")).toBeInTheDocument();
    expect(screen.queryByTestId("setup-form")).toBeNull();
  });

  it("shows the backend's password wording for a weak password and keeps the link for a retry", async () => {
    let attempts = 0;
    arrange(() => (++attempts === 1 ? reply(422, { detail: { code: "password_policy", message: "The password must be at least 12 characters." } }) : reply(200, { next: "/" })));
    openLink();
    withAssign();
    render(<SetupForm />);

    await fill("short pass", "short pass");
    expect((await screen.findByTestId("setup-error")).textContent).toBe("The password must be at least 12 characters.");

    await userEvent.clear(screen.getByLabelText("New password"));
    await userEvent.clear(screen.getByLabelText("Repeat the password"));
    await userEvent.type(screen.getByLabelText("New password"), PASSWORD);
    await userEvent.type(screen.getByLabelText("Repeat the password"), PASSWORD);
    await userEvent.click(screen.getByTestId("setup-submit"));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    const calls = fetchMock.mock.calls.filter(([url]) => url === "/api/auth/setup");
    expect(calls.map(([, init]) => JSON.parse(init.body).token)).toEqual([LINK, LINK]);
  });

  it.each([
    [429, "Too many attempts. Try again later."],
    [502, "Setting a password is unavailable right now."],
  ])("explains a %i without detail", async (status, message) => {
    arrange(() => reply(status, { detail: "secret" }));
    openLink();
    render(<SetupForm />);

    await fill();

    expect((await screen.findByTestId("setup-error")).textContent).toBe(message);
  });

  it("asks the password manager to create a new password", async () => {
    arrange();
    openLink();
    render(<SetupForm />);
    await waitFor(() => expect(screen.getByLabelText("New password")).toBeInTheDocument());
    expect(screen.getByLabelText("New password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Repeat the password")).toHaveAttribute("autocomplete", "new-password");
  });
});
