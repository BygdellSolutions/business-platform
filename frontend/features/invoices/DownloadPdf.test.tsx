import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { DownloadPdf, pdfFailureText } from "@/features/invoices/DownloadPdf";
import { Harness, INVOICE_ID, ORG_A, ORG_B, deferred, installBackend, invoice, issued, ok, resetServer, router } from "@/features/invoices/testing";
import { normalizeError, type ApiResult } from "@/lib/api/errors";
import type { DownloadedFile } from "@/lib/api/client";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn(), apiDownloadPdf: vi.fn() }));
import { apiDownloadPdf } from "@/lib/api/client";

const file = (filename = "invoice-7.pdf"): DownloadedFile => ({ blob: new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])], { type: "application/pdf" }), filename });
const failure = (status: number, body: unknown): ApiResult<DownloadedFile> => ({ ok: false, error: normalizeError(status, body) });

let clicked: { download: string; href: string }[];
let created: Blob[];
let revoked: string[];

beforeEach(() => {
  vi.mocked(apiDownloadPdf).mockReset();
  resetServer(invoice());
  installBackend(() => ok(null));
  clicked = [];
  created = [];
  revoked = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clicked.push({ download: this.download, href: this.href });
  });
  vi.stubGlobal("URL", Object.assign(URL, {
    createObjectURL: (blob: Blob) => {
      created.push(blob);
      return `blob:test/${created.length}`;
    },
    revokeObjectURL: (url: string) => revoked.push(url),
  }));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function view(status: "draft" | "issued" = "issued", orgId = ORG_A) {
  return render(
    <OrgScope orgId={orgId}>
      <DownloadPdf invoice={{ id: INVOICE_ID, status }} />
    </OrgScope>,
  );
}

describe("the Download PDF control", () => {
  it("is not there for a draft", () => {
    view("draft");
    expect(screen.queryByTestId("download-pdf")).toBeNull();
    expect(screen.queryByTestId("pdf")).toBeNull();
  });

  it("asks for the invoice's PDF in the current organization and saves the file under the filename it was given", async () => {
    vi.mocked(apiDownloadPdf).mockResolvedValue({ ok: true, status: 200, data: file() });
    view("issued", ORG_B);

    await userEvent.click(screen.getByTestId("download-pdf"));

    expect(apiDownloadPdf).toHaveBeenCalledTimes(1);
    expect(vi.mocked(apiDownloadPdf).mock.calls[0].slice(0, 2)).toEqual([ORG_B, `/invoices/${INVOICE_ID}/pdf`]);
    expect(clicked).toEqual([{ download: "invoice-7.pdf", href: "blob:test/1" }]);
    expect(created[0].type).toBe("application/pdf");
    expect(screen.getByTestId("pdf-done").textContent).toBe("Downloaded invoice-7.pdf.");
    expect(screen.queryByTestId("pdf-error")).toBeNull();
  });

  it("releases the temporary file address afterwards", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(apiDownloadPdf).mockResolvedValue({ ok: true, status: 200, data: file() });
      view();
      await userEvent.click(screen.getByTestId("download-pdf"));
      expect(revoked).toEqual([]);
      await act(async () => {
        vi.advanceTimersByTime(11_000);
      });
      expect(revoked).toEqual(["blob:test/1"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a pending state, cannot be pressed twice, and returns to normal afterwards", async () => {
    const gate = deferred<ApiResult<DownloadedFile>>();
    vi.mocked(apiDownloadPdf).mockReturnValue(gate.promise);
    view();

    await userEvent.click(screen.getByTestId("download-pdf"));

    const button = screen.getByTestId("download-pdf") as HTMLButtonElement;
    expect(button.textContent).toBe("Preparing PDF…");
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    await userEvent.click(button);
    expect(apiDownloadPdf).toHaveBeenCalledTimes(1);

    await act(async () => gate.resolve({ ok: true, status: 200, data: file() }));

    await waitFor(() => expect((screen.getByTestId("download-pdf") as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByTestId("download-pdf").textContent).toBe("Download PDF");
    expect(clicked).toHaveLength(1);
  });

  it.each([
    [404, { detail: "Not found" }, "no longer exists"],
    [409, { detail: { code: "invoice_not_issued", message: "x" } }, "Only an issued invoice has a PDF"],
    [500, { detail: "boom" }, "could not be prepared right now"],
  ])("explains a %s, saves nothing, and offers to try again", async (status, body, expected) => {
    vi.mocked(apiDownloadPdf).mockResolvedValue(failure(status, body));
    view();

    await userEvent.click(screen.getByTestId("download-pdf"));

    expect(screen.getByTestId("pdf-error").textContent).toContain(expected);
    expect(clicked).toEqual([]);
    expect(screen.getByTestId("download-pdf").textContent).toBe("Try again");
    expect((screen.getByTestId("download-pdf") as HTMLButtonElement).disabled).toBe(false);
  });

  it("explains a network failure without assuming anything", async () => {
    vi.mocked(apiDownloadPdf).mockResolvedValue({ ok: false, error: { kind: "network", status: 0, message: "offline" } });
    view();

    await userEvent.click(screen.getByTestId("download-pdf"));

    expect(screen.getByTestId("pdf-error").textContent).toContain("Nothing was changed; try again.");
  });

  it("lists the characters the renderer cannot draw, a limited number, and says how many more", async () => {
    const characters = Array.from({ length: 20 }, (_, index) => ({ character: `U+${(0x645 + index).toString(16).toUpperCase().padStart(4, "0")}`, reason: "needs complex text layout" }));
    vi.mocked(apiDownloadPdf).mockResolvedValue(
      failure(422, { detail: { code: "unsupported_characters", message: "This invoice contains characters the PDF renderer cannot draw correctly, so no PDF was created. This is a limitation of the renderer, not of the invoice.", characters, total: 25 } }),
    );
    view();

    await userEvent.click(screen.getByTestId("download-pdf"));

    expect(screen.getByTestId("pdf-error").textContent).toContain("limitation of the renderer, not of the invoice");
    const items = screen.getByTestId("pdf-error-details").querySelectorAll("li");
    expect(items).toHaveLength(9);
    expect(items[0].textContent).toBe("U+0645: needs complex text layout");
    expect(items[8].textContent).toBe("and 17 more");
  });

  it("a retry after a failure succeeds and clears the error", async () => {
    vi.mocked(apiDownloadPdf).mockResolvedValueOnce(failure(500, { detail: "boom" })).mockResolvedValueOnce({ ok: true, status: 200, data: file() });
    view();

    await userEvent.click(screen.getByTestId("download-pdf"));
    expect(screen.getByTestId("pdf-error")).toBeTruthy();
    await userEvent.click(screen.getByTestId("download-pdf"));

    expect(screen.queryByTestId("pdf-error")).toBeNull();
    expect(clicked).toHaveLength(1);
    expect(apiDownloadPdf).toHaveBeenCalledTimes(2);
  });

  it("ignores an answer that arrives after the screen was left: nothing is saved and nothing is set", async () => {
    const gate = deferred<ApiResult<DownloadedFile>>();
    vi.mocked(apiDownloadPdf).mockReturnValue(gate.promise);
    const { unmount } = view();

    await userEvent.click(screen.getByTestId("download-pdf"));
    const signal = vi.mocked(apiDownloadPdf).mock.calls[0][2] as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => gate.resolve({ ok: true, status: 200, data: file() }));

    expect(clicked).toEqual([]);
  });
});

describe("pdfFailureText", () => {
  it("covers every kind of error with a plain sentence", () => {
    for (const status of [401, 403, 404, 409, 422, 500, 418]) {
      const { text } = pdfFailureText(normalizeError(status, undefined));
      expect(text.length).toBeGreaterThan(10);
    }
  });
});

describe("on the invoice page", () => {
  it("an issued invoice has the control, for a reader as well as a mutator; a draft does not", () => {
    for (const canMutate of [true, false]) {
      const { unmount } = render(<Harness initial={issued()} canMutate={canMutate} />);
      expect(screen.getByTestId("download-pdf")).toBeTruthy();
      unmount();
    }
    render(<Harness initial={invoice()} />);
    expect(screen.queryByTestId("download-pdf")).toBeNull();
  });
});
