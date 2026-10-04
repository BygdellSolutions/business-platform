import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SignOut } from "@/components/shell/SignOut";

const CSRF = "C".repeat(43);

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;
let originalLocation: Location;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  assign = vi.fn();
  originalLocation = window.location;
  Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, assign } });
  document.cookie = `bp_csrf=${CSRF}; path=/`;
});
afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  document.cookie = "bp_csrf=; Max-Age=0; path=/";
  vi.unstubAllGlobals();
});

const reply = (status: number, body: unknown = {}) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

describe("SignOut in session mode", () => {
  it("posts to the BFF with the CSRF token from the readable cookie, then does a full page load of the login page", async () => {
    fetchMock.mockReturnValue(reply(200, { revoked: "confirmed" }));
    render(<SignOut mode="session" />);

    await userEvent.click(screen.getByTestId("sign-out"));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login?notice=signed-out"));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { headers: Record<string, string> }];
    expect(url).toBe("/api/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "x-csrf-token": CSRF });
    expect(init.credentials).toBe("same-origin");
  });

  it.each([
    ["the server could not confirm", () => reply(200, { revoked: "unconfirmed" })],
    ["the BFF refused", () => reply(403, {})],
    ["the network failed", () => Promise.reject(new TypeError("fetch failed"))],
  ])("still leaves the page, but does not claim the server ended the session when %s", async (_name, answer) => {
    fetchMock.mockImplementation(answer);
    render(<SignOut mode="session" />);

    await userEvent.click(screen.getByTestId("sign-out"));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login?notice=signed-out-unconfirmed"));
  });

  it("an already-invalid session is simply signed out", async () => {
    fetchMock.mockReturnValue(reply(200, { revoked: "already_invalid" }));
    render(<SignOut mode="session" />);
    await userEvent.click(screen.getByTestId("sign-out"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login?notice=signed-out"));
  });

  it("cannot be pressed twice", async () => {
    let finish: (response: Response) => void = () => {};
    fetchMock.mockReturnValue(new Promise<Response>((resolve) => (finish = resolve)));
    render(<SignOut mode="session" />);

    await userEvent.click(screen.getByTestId("sign-out"));

    expect(screen.getByTestId("sign-out")).toBeDisabled();
    expect(screen.getByTestId("sign-out").textContent).toBe("Signing out…");
    finish(new Response(JSON.stringify({ revoked: "confirmed" }), { status: 200 }));
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends no CSRF header when there is no CSRF cookie (the BFF will refuse, and the login page says it could not confirm)", async () => {
    document.cookie = "bp_csrf=; Max-Age=0; path=/";
    fetchMock.mockReturnValue(reply(403, {}));
    render(<SignOut mode="session" />);

    await userEvent.click(screen.getByTestId("sign-out"));

    expect((fetchMock.mock.calls[0][1] as { headers: Record<string, string> }).headers).toEqual({});
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login?notice=signed-out-unconfirmed"));
  });
});

describe("SignOut in dev mode", () => {
  it("is the existing dev-session form, and nothing is fetched", () => {
    const { container } = render(<SignOut mode="dev" />);
    const form = container.querySelector("form")!;
    expect(form.getAttribute("action")).toBe("/api/dev-session");
    expect(form.getAttribute("method")).toBe("post");
    expect((form.querySelector("input[name=logout]") as HTMLInputElement).value).toBe("1");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
