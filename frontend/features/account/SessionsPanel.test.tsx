import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionsPanel } from "@/features/account/SessionsPanel";
import type { OwnSession } from "@/lib/auth/own-sessions";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));

const HERE: OwnSession = { id: "11111111-1111-4111-8111-111111111111", createdAt: "2026-10-15T08:00:00Z", lastUsedAt: "2026-10-15T09:30:00Z", userAgent: "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537", current: true };
const PHONE: OwnSession = { id: "22222222-2222-4222-8222-222222222222", createdAt: "2026-10-14T20:00:00Z", lastUsedAt: "2026-10-14T21:00:00Z", userAgent: "Mozilla/5.0 (iPhone) Version/17 Safari/604", current: false };

let fetchMock: ReturnType<typeof vi.fn>;
const reply = (status: number, body: unknown = {}) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

beforeEach(() => {
  push.mockReset();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  document.cookie = "bp_csrf=" + "C".repeat(43);
});
afterEach(() => vi.unstubAllGlobals());

describe("where you are signed in", () => {
  it("lists the sessions, marks this browser and offers no sign-out for it", () => {
    render(<SessionsPanel initial={[HERE, PHONE]} />);

    const rows = screen.getAllByTestId("session-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Chrome on Windows")).toBeInTheDocument();
    expect(within(rows[0]).getByText("This browser")).toBeInTheDocument();
    expect(within(rows[0]).queryByTestId("end-session")).toBeNull();
    expect(within(rows[1]).getByText("Safari on iOS")).toBeInTheDocument();
    expect(within(rows[1]).getByTestId("end-session")).toBeInTheDocument();
  });

  it("ends one session with the CSRF token, then shows what the server has", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      init?.method === "DELETE" ? reply(200, { ended: 1 }) : reply(200, { sessions: [HERE] }),
    );
    render(<SessionsPanel initial={[HERE, PHONE]} />);

    await userEvent.click(screen.getByTestId("end-session"));

    expect(await screen.findByTestId("sessions-done")).toHaveTextContent("That session was signed out.");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { headers: Record<string, string> }];
    expect(url).toBe(`/api/auth/sessions/${PHONE.id}`);
    expect(init.method).toBe("DELETE");
    expect(init.headers["x-csrf-token"]).toBe("C".repeat(43));
    expect(await screen.findAllByTestId("session-row")).toHaveLength(1); // the list was read again
  });

  it("signs out the others, and the button is off when there are none", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      init?.method === "POST" ? reply(200, { ended: 1 }) : reply(200, { sessions: [HERE] }),
    );
    const { unmount } = render(<SessionsPanel initial={[HERE]} />);
    expect(screen.getByTestId("end-others")).toBeDisabled();
    unmount();

    render(<SessionsPanel initial={[HERE, PHONE]} />);
    await userEvent.click(screen.getByTestId("end-others"));

    expect(await screen.findByTestId("sessions-done")).toHaveTextContent("Every other session was signed out.");
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/auth/sessions/end-others");
  });

  it("signing out everywhere goes to the sign-in page", async () => {
    fetchMock.mockImplementation(() => reply(200, { ended: 2 }));
    render(<SessionsPanel initial={[HERE, PHONE]} />);

    await userEvent.click(screen.getByTestId("end-all"));

    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/login"));
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/auth/sessions/end-all");
  });

  it("does NOT claim success or leave the page when the server refuses", async () => {
    fetchMock.mockImplementation(() => reply(502, { message: "x" }));
    render(<SessionsPanel initial={[HERE, PHONE]} />);

    await userEvent.click(screen.getByTestId("end-all"));

    expect(await screen.findByTestId("sessions-error")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
    expect(screen.queryByTestId("sessions-done")).toBeNull();
  });

  it("a double click sends one request (single flight)", async () => {
    let release: () => void = () => undefined;
    fetchMock.mockImplementation(() => new Promise((resolve) => (release = () => resolve(new Response(JSON.stringify({ ended: 1 }), { status: 200 })))));
    render(<SessionsPanel initial={[HERE, PHONE]} />);
    const button = screen.getByTestId("end-others");

    await userEvent.dblClick(button);
    release();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
