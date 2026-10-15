import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountDataPanel } from "@/features/account/AccountDataPanel";

let fetchMock: ReturnType<typeof vi.fn>;
let click: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  URL.createObjectURL = vi.fn(() => "blob:data");
  URL.revokeObjectURL = vi.fn();
  click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
  click.mockRestore();
});

describe("download my account data", () => {
  it("saves the copy under its own name and says so", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response('{"profile":{}}', { status: 200 })));
    render(<AccountDataPanel />);

    await userEvent.click(screen.getByTestId("download-account-data"));

    expect(await screen.findByTestId("account-data-done")).toHaveTextContent("account-data.json");
    expect(click).toHaveBeenCalledTimes(1);
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/auth/account-export");
  });

  it.each([
    [429, /several copies/],
    [502, /could not be downloaded/],
  ])("explains a %s and saves nothing", async (status, text) => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response("{}", { status })));
    render(<AccountDataPanel />);

    await userEvent.click(screen.getByTestId("download-account-data"));

    expect(await screen.findByTestId("account-data-error")).toHaveTextContent(text);
    expect(click).not.toHaveBeenCalled();
  });

  it("a double click asks once (single flight)", async () => {
    let release: () => void = () => undefined;
    fetchMock.mockImplementation(() => new Promise((resolve) => (release = () => resolve(new Response("{}", { status: 200 })))));
    render(<AccountDataPanel />);

    await userEvent.dblClick(screen.getByTestId("download-account-data"));
    release();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
