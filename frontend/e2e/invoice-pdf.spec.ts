import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

import { expect, test, type Page } from "./fixtures";

import { bffUrl, createCompletedTransaction, createInvoiceApi, createWorld, insertCustomer, issueInvoiceApi, signIn, sql, testRow, type RoleName, type World } from "./support";

/**
 * The invoice PDF in a real browser, through the real BFF and backend.
 *
 * The first download makes the PDF and stores it; every later download is the stored file, byte for byte.
 * The browser must receive a real file download (the Playwright download event), named from the invoice
 * number, whose bytes are exactly the artifact stored in the database.
 */

let world: World;
test.afterEach(() => world?.cleanup());

const sha256 = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
const stored = (invoiceId: string) => testRow(`select count(*) || '|' || coalesce(max(sha256), '') || '|' || coalesce(max(byte_size), 0) || '|' || coalesce(max(id::text), '') from invoice_pdfs where invoice_id = ${sql(invoiceId)}`).split("|");

async function build(context: Parameters<typeof signIn>[0], options: { lineDescription?: string } = {}) {
  world = createWorld({ label: "InvoicePdf" });
  await signIn(context, world.email);
  const customer = insertCustomer(world.orgId, "Umeå HK");
  const lines = options.lineDescription ? [{ description: options.lineDescription, unit: "session", quantity: "1", unit_price_ex_vat: "850.00", vat_rate: "25" }] : undefined;
  const t1 = await createCompletedTransaction(context, world.orgId, customer, { date: "2026-10-01", lines });
  const t2 = await createCompletedTransaction(context, world.orgId, customer, { date: "2026-10-02" });
  const draft = await createInvoiceApi(context, world.orgId, [t1.id], { description: "Draft" });
  const issued = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [t2.id], { description: "Åke sjukgymnastik för Östen" }));
  return { customer, draft, issued };
}

async function downloadFromPage(page: Page) {
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("download-pdf").click()]);
  const path = await download.path();
  return { download, bytes: readFileSync(path) };
}

test.describe("the browser downloads the stored PDF", () => {
  test("a real file download, named from the number, whose bytes are the artifact in the database", async ({ page, context }) => {
    const s = await build(context);
    expect(stored(s.issued.id)[0]).toBe("0"); // nothing is made before somebody asks
    await page.goto(`/o/${world.orgId}/invoices/${s.issued.id}`);

    const { download, bytes } = await downloadFromPage(page);

    expect(download.suggestedFilename()).toBe("invoice-1001.pdf");
    expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
    const [count, sha, size] = stored(s.issued.id);
    expect(count).toBe("1");
    expect(sha256(bytes)).toBe(sha);
    expect(String(bytes.length)).toBe(size);
    await expect(page.getByTestId("pdf-done")).toHaveText("Downloaded invoice-1001.pdf.");
    await expect(page.getByTestId("pdf-error")).toHaveCount(0);
  });

  test("a second download is the same stored file, and changing the live data afterwards changes nothing", async ({ page, context }) => {
    const s = await build(context);
    await page.goto(`/o/${world.orgId}/invoices/${s.issued.id}`);
    const first = await downloadFromPage(page);
    const [, , , rowId] = stored(s.issued.id);

    testRow(`update customers set name = 'Renamed Club' where id = ${sql(s.customer)}`);
    testRow(`update organizations set name = 'Renamed Org' where id = ${sql(world.orgId)}`);
    await page.reload();
    const second = await downloadFromPage(page);

    expect(sha256(second.bytes)).toBe(sha256(first.bytes));
    expect(second.bytes.equals(first.bytes)).toBe(true);
    const [count, , , rowIdAfter] = stored(s.issued.id);
    expect([count, rowIdAfter]).toEqual(["1", rowId]); // still the one canonical artifact
  });

  test("a draft has no PDF control, and asking the BFF for one gets a clear refusal, not a file", async ({ page, context }) => {
    const s = await build(context);
    await page.goto(`/o/${world.orgId}/invoices/${s.draft.id}`);
    await expect(page.getByTestId("invoice-view")).toBeVisible();
    await expect(page.getByTestId("download-pdf")).toHaveCount(0);

    const response = await context.request.get(bffUrl(world.orgId, `/invoices/${s.draft.id}/pdf`));
    expect(response.status()).toBe(409);
    expect(response.headers()["content-type"]).toContain("application/json");
    expect((await response.json()).detail.code).toBe("invoice_not_issued");
    expect(stored(s.draft.id)[0]).toBe("0");
  });

  for (const role of ["employee", "viewer"] as RoleName[]) {
    test(`a ${role} (who can only read) can download, and that first download makes the PDF`, async ({ page, context }) => {
      const s = await build(context);
      await signIn(context, world.addMember(role));
      await page.goto(`/o/${world.orgId}/invoices/${s.issued.id}`);
      await expect(page.getByTestId("no-mutation")).toHaveCount(0); // (an issued invoice has no mutation controls at all)

      const { download, bytes } = await downloadFromPage(page);

      expect(download.suggestedFilename()).toBe("invoice-1001.pdf");
      expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
      expect(stored(s.issued.id)[0]).toBe("1");
    });
  }
});

test.describe("what the BFF puts on the wire", () => {
  test("the response is a hardened attachment whose ETag is the SHA-256 of the body", async ({ context }) => {
    const s = await build(context);

    const response = await context.request.get(bffUrl(world.orgId, `/invoices/${s.issued.id}/pdf`));

    expect(response.status()).toBe(200);
    const headers = response.headers();
    expect(headers["content-type"]).toBe("application/pdf");
    expect(headers["content-disposition"]).toBe('attachment; filename="invoice-1001.pdf"');
    expect(headers["cache-control"]).toBe("private, no-store");
    expect(headers["x-content-type-options"]).toBe("nosniff");
    const body = await response.body();
    expect(headers["content-length"]).toBe(String(body.length));
    expect(headers["etag"]).toBe(`"${sha256(body)}"`);
    expect(body.subarray(0, 5).toString("latin1")).toBe("%PDF-");
    expect(stored(s.issued.id)[1]).toBe(sha256(body));
  });

  test("only a plain GET of /invoices/{id}/pdf is the PDF path", async ({ context }) => {
    const s = await build(context);
    const url = bffUrl(world.orgId, `/invoices/${s.issued.id}/pdf`);

    expect((await context.request.get(`${url}?x=1`)).status()).toBe(404);
    expect((await context.request.post(url)).status()).toBe(404);
    expect((await context.request.delete(url)).status()).toBe(404);
    expect(stored(s.issued.id)[0]).toBe("0"); // none of that made a PDF
  });
});

test.describe("tenant isolation", () => {
  test("another organization's invoice and a random id are the same JSON 404, never a file", async ({ context }) => {
    const s = await build(context);
    const other = createWorld({ label: "InvoicePdfOther" });
    try {
      await signIn(context, other.email);
      const cross = await context.request.get(bffUrl(other.orgId, `/invoices/${s.issued.id}/pdf`)); // my invoice id, their organization
      const random = await context.request.get(bffUrl(other.orgId, `/invoices/${randomUUID()}/pdf`));
      const notMember = await context.request.get(bffUrl(world.orgId, `/invoices/${s.issued.id}/pdf`)); // their login, my organization

      expect([cross.status(), random.status(), notMember.status()]).toEqual([404, 404, 404]);
      expect(await cross.json()).toEqual(await random.json());
      for (const response of [cross, random, notMember]) expect(response.headers()["content-type"]).toContain("application/json");
      expect(stored(s.issued.id)[0]).toBe("0"); // and nothing was generated on behalf of an outsider
    } finally {
      other.cleanup();
    }
  });
});

test.describe("what the user sees", () => {
  test("shows a pending state while the PDF is being prepared, then downloads it", async ({ page, context }) => {
    const s = await build(context);
    await page.goto(`/o/${world.orgId}/invoices/${s.issued.id}`);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/invoices/*/pdf", async (route) => {
      await gate;
      await route.continue();
    });

    const download = page.waitForEvent("download");
    await page.getByTestId("download-pdf").click();

    await expect(page.getByTestId("download-pdf")).toHaveText("Preparing PDF…");
    await expect(page.getByTestId("download-pdf")).toBeDisabled();
    release();
    expect((await download).suggestedFilename()).toBe("invoice-1001.pdf");
    await expect(page.getByTestId("download-pdf")).toHaveText("Download PDF");
    await expect(page.getByTestId("download-pdf")).toBeEnabled();
  });

  test("a server error is explained, nothing is downloaded, and trying again works", async ({ page, context }) => {
    const s = await build(context);
    await page.goto(`/o/${world.orgId}/invoices/${s.issued.id}`);
    let failing = true;
    await page.route("**/invoices/*/pdf", async (route) => {
      if (failing) await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "boom" }) });
      else await route.continue();
    });
    let downloads = 0;
    page.on("download", () => downloads++);

    await page.getByTestId("download-pdf").click();

    await expect(page.getByTestId("pdf-error")).toContainText("could not be prepared right now");
    await expect(page.getByTestId("download-pdf")).toHaveText("Try again");
    expect(downloads).toBe(0);

    failing = false;
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("download-pdf").click()]);
    expect(download.suggestedFilename()).toBe("invoice-1001.pdf");
    await expect(page.getByTestId("pdf-error")).toHaveCount(0);
  });

  test("a 404 and a 409 are explained in plain words", async ({ page, context }) => {
    const s = await build(context);
    await page.goto(`/o/${world.orgId}/invoices/${s.issued.id}`);
    let answer: { status: number; body: unknown } = { status: 404, body: { detail: "Not found" } };
    await page.route("**/invoices/*/pdf", (route) => route.fulfill({ status: answer.status, contentType: "application/json", body: JSON.stringify(answer.body) }));

    await page.getByTestId("download-pdf").click();
    await expect(page.getByTestId("pdf-error")).toContainText("no longer exists, or you do not have access");

    answer = { status: 409, body: { detail: { code: "invoice_not_issued", message: "x" } } };
    await page.getByTestId("download-pdf").click();
    await expect(page.getByTestId("pdf-error")).toContainText("Only an issued invoice has a PDF");
  });

  test("a response that is not a PDF is never saved as one", async ({ page, context }) => {
    const s = await build(context);
    await page.goto(`/o/${world.orgId}/invoices/${s.issued.id}`);
    await page.route("**/invoices/*/pdf", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<html>login</html>" }));
    let downloads = 0;
    page.on("download", () => downloads++);

    await page.getByTestId("download-pdf").click();

    await expect(page.getByTestId("pdf-error")).toBeVisible();
    expect(downloads).toBe(0);
  });

  test("text the renderer cannot draw is refused with the characters named, and nothing is stored", async ({ page, context }) => {
    const s = await build(context, { lineDescription: "مرحبا" });
    // The invoice with the Arabic line is the DRAFT built from t1; issue it so it can have a PDF.
    const issued = await issueInvoiceApi(context, world.orgId, s.draft);
    await page.goto(`/o/${world.orgId}/invoices/${issued.id}`);
    let downloads = 0;
    page.on("download", () => downloads++);

    await page.getByTestId("download-pdf").click();

    await expect(page.getByTestId("pdf-error")).toContainText("limitation of the renderer, not of the invoice");
    await expect(page.getByTestId("pdf-error-details")).toContainText("U+0645");
    expect(downloads).toBe(0);
    expect(stored(issued.id)[0]).toBe("0");
    await expect(page.getByTestId("download-pdf")).toHaveText("Try again");
  });
});
