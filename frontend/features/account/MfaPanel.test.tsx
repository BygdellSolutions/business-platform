import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MfaPanel } from "@/features/account/MfaPanel";
import type { MfaStatus } from "@/lib/auth/own-mfa";

// jsdom has no canvas: the real QR drawing is exercised by the browser test.
vi.mock("qrcode", () => ({ default: { toDataURL: vi.fn(async () => "data:image/png;base64,QUJD") } }));

const OFF: MfaStatus = { enabled: false, required: false, recoveryCodesLeft: 0 };
const ON: MfaStatus = { enabled: true, required: false, recoveryCodesLeft: 8 };
const SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
const URI = `otpauth://totp/Business%20Platform%3Aada%40example.test?secret=${SECRET}`;
const CODES = Array.from({ length: 10 }, (_, i) => `${String(i).repeat(8)}-abcdef01`);

let fetchMock: ReturnType<typeof vi.fn>;
const reply = (status: number, body: unknown = {}) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
const calls = () => fetchMock.mock.calls as [string, RequestInit & { headers: Record<string, string> }][];

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  document.cookie = "bp_csrf=" + "C".repeat(43);
});
afterEach(() => vi.unstubAllGlobals());

describe("turning the second factor on", () => {
  it("asks for the password, shows the key and a QR code, then the recovery codes once", async () => {
    fetchMock.mockImplementation((url: string) =>
      url === "/api/auth/mfa/enroll"
        ? reply(200, { enrollment: { secret: SECRET, uri: URI } })
        : url === "/api/auth/mfa/confirm"
          ? reply(200, { recoveryCodes: CODES })
          : reply(200, { status: ON }),
    );
    render(<MfaPanel initial={OFF} mustSetUp={false} />);

    await userEvent.click(screen.getByTestId("mfa-set-up"));
    await userEvent.type(screen.getByTestId("mfa-password"), "my password");
    await userEvent.click(screen.getByTestId("mfa-password-submit"));

    expect(await screen.findByTestId("mfa-secret")).toHaveTextContent(SECRET);
    expect(screen.getByTestId("mfa-qr")).toHaveAttribute("src", "data:image/png;base64,QUJD");
    expect(calls()[0][0]).toBe("/api/auth/mfa/enroll");
    expect(JSON.parse(calls()[0][1].body as string)).toEqual({ password: "my password" });
    expect(calls()[0][1].headers["x-csrf-token"]).toBe("C".repeat(43));

    await userEvent.type(screen.getByTestId("mfa-code"), "123456");
    await userEvent.click(screen.getByTestId("mfa-confirm"));

    expect(await screen.findAllByTestId("mfa-recovery-code")).toHaveLength(10);
    expect(JSON.parse(calls()[1][1].body as string)).toEqual({ code: "123456" });
    expect(screen.queryByTestId("mfa-secret")).toBeNull(); // the key is gone once the factor is on

    await userEvent.click(screen.getByTestId("mfa-saved"));

    expect(await screen.findByTestId("mfa-state")).toHaveTextContent("On. 8 recovery codes left.");
    expect(screen.queryByTestId("mfa-recovery-code")).toBeNull(); // never shown again
    expect(document.body.innerHTML).not.toContain(CODES[0]);
  });

  it.each([
    ["wrong_password", "The password is not correct."],
    ["invalid_code", "That code is not correct."],
  ])("explains %s in plain words", async (code, text) => {
    fetchMock.mockImplementation(() => reply(400, { detail: { code, message: "x" } }));
    render(<MfaPanel initial={OFF} mustSetUp={false} />);

    await userEvent.click(screen.getByTestId("mfa-set-up"));
    await userEvent.type(screen.getByTestId("mfa-password"), "nope");
    await userEvent.click(screen.getByTestId("mfa-password-submit"));

    expect(await screen.findByTestId("mfa-error")).toHaveTextContent(text);
    expect(screen.queryByTestId("mfa-secret")).toBeNull();
  });

  it("tells an owner or admin why they are here", () => {
    render(<MfaPanel initial={{ ...OFF, required: true }} mustSetUp />);

    expect(screen.getByTestId("mfa-required-notice")).toHaveTextContent(/requires a second factor/);
  });
});

describe("when it is on", () => {
  it("shows how many recovery codes are left and offers new codes and turning it off", () => {
    render(<MfaPanel initial={{ ...ON, recoveryCodesLeft: 1 }} mustSetUp={false} />);

    expect(screen.getByTestId("mfa-state")).toHaveTextContent("On. 1 recovery code left.");
    expect(screen.getByTestId("mfa-new-codes")).toBeEnabled();
    expect(screen.getByTestId("mfa-turn-off")).toBeEnabled();
  });

  it("cannot be turned off by someone whose role requires it", () => {
    render(<MfaPanel initial={{ ...ON, required: true }} mustSetUp={false} />);

    expect(screen.getByTestId("mfa-turn-off")).toBeDisabled();
  });

  it("turns off with a current code and then shows it as off", async () => {
    fetchMock.mockImplementation((url: string) => (url === "/api/auth/mfa/disable" ? reply(200, { disabled: true }) : reply(200, { status: OFF })));
    render(<MfaPanel initial={ON} mustSetUp={false} />);

    await userEvent.click(screen.getByTestId("mfa-turn-off"));
    await userEvent.type(screen.getByTestId("mfa-current-code"), "123456");
    await userEvent.click(screen.getByTestId("mfa-current-submit"));

    expect(await screen.findByTestId("mfa-done")).toHaveTextContent("The second factor is off.");
    expect(await screen.findByTestId("mfa-set-up")).toBeInTheDocument();
    expect(JSON.parse(calls()[0][1].body as string)).toEqual({ code: "123456" });
  });

  it("gets new recovery codes with a current code, and the old ones are not shown", async () => {
    fetchMock.mockImplementation(() => reply(200, { recoveryCodes: CODES }));
    render(<MfaPanel initial={ON} mustSetUp={false} />);

    await userEvent.click(screen.getByTestId("mfa-new-codes"));
    await userEvent.type(screen.getByTestId("mfa-current-code"), "abcd1234-abcd1234");
    await userEvent.click(screen.getByTestId("mfa-current-submit"));

    expect(await screen.findAllByTestId("mfa-recovery-code")).toHaveLength(10);
    expect(calls()[0][0]).toBe("/api/auth/mfa/recovery-codes");
  });

  it("says so and keeps the factor on when the server refuses", async () => {
    fetchMock.mockImplementation(() => reply(409, { detail: { code: "required" } }));
    render(<MfaPanel initial={ON} mustSetUp={false} />);

    await userEvent.click(screen.getByTestId("mfa-turn-off"));
    await userEvent.type(screen.getByTestId("mfa-current-code"), "123456");
    await userEvent.click(screen.getByTestId("mfa-current-submit"));

    expect(await screen.findByTestId("mfa-error")).toHaveTextContent("requires a second factor");
    expect(screen.queryByTestId("mfa-done")).toBeNull();
  });

  it("a double click sends one request (single flight)", async () => {
    let release: () => void = () => undefined;
    fetchMock.mockImplementation(() => new Promise((resolve) => (release = () => resolve(new Response(JSON.stringify({ recoveryCodes: CODES }), { status: 200 })))));
    render(<MfaPanel initial={ON} mustSetUp={false} />);
    await userEvent.click(screen.getByTestId("mfa-new-codes"));
    await userEvent.type(screen.getByTestId("mfa-current-code"), "123456");

    await userEvent.dblClick(screen.getByTestId("mfa-current-submit"));
    release();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
