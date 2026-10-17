import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChangePasswordForm } from "@/features/account/ChangePasswordForm";

let fetchMock: ReturnType<typeof vi.fn>;
const reply = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

async function fill(current: string, next: string, again: string) {
  await userEvent.type(screen.getByLabelText("Current password"), current);
  await userEvent.type(screen.getByLabelText("New password"), next);
  await userEvent.type(screen.getByLabelText("New password again"), again);
  await userEvent.click(screen.getByTestId("change-password"));
}

describe("changing the password", () => {
  it("sends the current and the new password to the BFF and says other sessions ended", async () => {
    fetchMock.mockImplementation(() => reply(200, { changed: true }));
    render(<ChangePasswordForm />);
    await fill("old password", "a brand new passphrase", "a brand new passphrase");
    expect(await screen.findByTestId("password-changed")).toHaveTextContent(/every other session was signed out/);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/auth/change-password");
    expect(JSON.parse(String(init.body))).toEqual({ current_password: "old password", new_password: "a brand new passphrase" });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
  });

  it("sends nothing when the two new entries differ", async () => {
    render(<ChangePasswordForm />);
    await fill("old password", "one passphrase", "another passphrase");
    expect(screen.getByTestId("password-error")).toHaveTextContent(/not the same/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the backend's own message (wrong current password, policy)", async () => {
    fetchMock.mockImplementation(() => reply(400, { code: "wrong_current_password", message: "The current password is not correct." }));
    render(<ChangePasswordForm />);
    await fill("wrong", "a brand new passphrase", "a brand new passphrase");
    expect(await screen.findByTestId("password-error")).toHaveTextContent("The current password is not correct.");
  });
});
