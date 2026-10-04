import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/features/auth/LoginForm";

const PRE = "P".repeat(43);
const ORG = "00000000-0000-4000-8000-0000000000a1";

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;
let originalLocation: Location;

const reply = (status: number, body: unknown = {}) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

function arrange(loginResponse: () => Promise<Response> = () => reply(200, { next: "/" })) {
  fetchMock.mockImplementation((url: string) => (url === "/api/auth/pre" ? reply(200, { token: PRE }) : loginResponse()));
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  assign = vi.fn();
  originalLocation = window.location;
  Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, assign } });
});
afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  vi.unstubAllGlobals();
});

async function signIn(email = "ada@example.test", password = "correct horse battery") {
  await waitFor(() => expect(screen.getByTestId("login-submit")).toBeEnabled());
  await userEvent.type(screen.getByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password"), password);
  await userEvent.click(screen.getByTestId("login-submit"));
}

function loginCall() {
  return fetchMock.mock.calls.find(([url]) => url === "/api/auth/login") as [string, RequestInit & { headers: Record<string, string> }] | undefined;
}

describe("LoginForm", () => {
  it("prepares a pre-auth secret before it can be submitted", async () => {
    arrange();
    render(<LoginForm next="/" />);

    expect(screen.getByTestId("login-submit")).toBeDisabled();
    await waitFor(() => expect(screen.getByTestId("login-submit")).toBeEnabled());
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/pre", expect.objectContaining({ cache: "no-store", credentials: "same-origin" }));
  });

  it("sends the credentials in a JSON body with the pre-auth header, and nothing secret in the URL or any other header", async () => {
    arrange();
    render(<LoginForm next={`/o/${ORG}`} />);

    await signIn();

    const [url, init] = loginCall()!;
    expect(url).toBe("/api/auth/login");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "content-type": "application/json", "x-pre-auth": PRE });
    expect(JSON.parse(init.body as string)).toEqual({ email: "ada@example.test", password: "correct horse battery", next: `/o/${ORG}` });
    for (const [calledUrl] of fetchMock.mock.calls) expect(String(calledUrl)).not.toMatch(/correct|ada@|battery/);
  });

  it("goes to the destination the BFF returned, with a full page load", async () => {
    arrange(() => reply(200, { next: `/o/${ORG}/customers` }));
    render(<LoginForm next="/" />);

    await signIn();

    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/o/${ORG}/customers`));
  });

  it.each(["https://evil.example", "//evil.example", "javascript:alert(1)", "", undefined, 42])("never navigates to a destination that is not a relative path (%j)", async (next) => {
    arrange(() => reply(200, { next }));
    render(<LoginForm next="/" />);

    await signIn();

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
  });

  it("shows ONE message for a wrong password, an unknown email or a disabled account, and clears the password", async () => {
    arrange(() => reply(401, { detail: "Invalid email or password" }));
    render(<LoginForm next="/" />);

    await signIn("ghost@example.test", "whatever long password");

    expect((await screen.findByTestId("login-error")).textContent).toBe("Invalid email or password.");
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
    expect(assign).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toMatch(/unknown|no such|disabled|not found/i);
  });

  it.each([
    [429, "Too many attempts. Try again later."],
    [503, "The service is busy. Try again in a moment."],
    [502, "Sign-in is unavailable right now."],
    [500, "Sign-in is unavailable right now."],
  ])("explains a %i without detail", async (status, message) => {
    arrange(() => reply(status, { detail: { message: "internal secret" } }));
    render(<LoginForm next="/" />);

    await signIn();

    expect((await screen.findByTestId("login-error")).textContent).toBe(message);
    expect(document.body.textContent).not.toContain("secret");
  });

  it("an expired pre-auth secret (403) asks to try again and fetches a new one", async () => {
    arrange(() => reply(403, { detail: { code: "pre_auth_failed" } }));
    render(<LoginForm next="/" />);

    await signIn();

    expect((await screen.findByTestId("login-error")).textContent).toBe("This page has expired. Try again.");
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === "/api/auth/pre").length).toBe(2));
  });

  it("a network failure is reported without claiming anything about the account", async () => {
    arrange(() => Promise.reject(new TypeError("fetch failed")));
    render(<LoginForm next="/" />);

    await signIn();

    expect((await screen.findByTestId("login-error")).textContent).toMatch(/Could not reach the server/);
  });

  it("does not submit without a pre-auth secret, and says the page could not be prepared", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new TypeError("fetch failed")));
    render(<LoginForm next="/" />);

    expect(await screen.findByTestId("login-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("login-submit")).toBeDisabled();
  });

  it("shows only the known notices", () => {
    arrange();
    const { rerender } = render(<LoginForm next="/" notice="signed-out-unconfirmed" />);
    expect(screen.getByTestId("login-notice").textContent).toMatch(/could not confirm/);
    rerender(<LoginForm next="/" notice="<img src=x onerror=alert(1)>" />);
    expect(screen.queryByTestId("login-notice")).toBeNull();
  });

  it("uses no browser storage and keeps neither the password nor the pre-auth secret in the page", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    arrange();
    render(<LoginForm next="/" />);

    await signIn();

    expect(setItem).not.toHaveBeenCalled();
    expect(document.body.innerHTML).not.toContain(PRE);
    expect(document.body.innerHTML).not.toContain("correct horse battery");
    setItem.mockRestore();
  });

  it("asks the browser to offer the saved username and password, not to autofill a new one", () => {
    arrange();
    render(<LoginForm next="/" />);
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "current-password");
  });
});
