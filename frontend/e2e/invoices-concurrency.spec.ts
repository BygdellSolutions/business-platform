import { expect, test, type BrowserContext, type Page } from "./fixtures";

import { bffUrl, createCompletedTransaction, createInvoiceApi, createWorld, getInvoiceApi, ifMatch, insertCustomer, signIn, sql, testRow, type World } from "./support";

/**
 * Optimistic concurrency and lost responses as the BROWSER meets them: two tabs of one user (and
 * API calls standing in for "someone else"), against the real backend and the test database. A change
 * is refused when it was based on an old version and changes nothing; an open editor never loses
 * what the user typed; and a request whose answer was lost is checked, never assumed.
 */

let world: World;
test.afterEach(() => world?.cleanup());

const url = (invoiceId: string) => `/o/${world.orgId}/invoices/${invoiceId}`;
const status = (page: Page) => page.getByTestId("invoice-status");

async function becomeVisible(page: Page) {
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

/** One draft invoice, opened in two tabs. */
async function twoTabs(context: BrowserContext) {
  world = createWorld({ label: "InvoiceTabs" });
  await signIn(context, world.email);
  const customer = insertCustomer(world.orgId, "Umeå HK");
  const tx = await createCompletedTransaction(context, world.orgId, customer);
  const invoice = await createInvoiceApi(context, world.orgId, [tx.id], { invoice_date: "2026-10-01", description: "Original" });
  const a = await context.newPage();
  const b = await context.newPage();
  await a.goto(url(invoice.id));
  await b.goto(url(invoice.id));
  await expect(status(a)).toHaveText("Draft");
  await expect(status(b)).toHaveText("Draft");
  return { a, b, invoice, customer };
}

const description = (invoiceId: string) => testRow(`select coalesce(description, '-') from invoices where id = ${sql(invoiceId)}`);

test.describe("a stale tab cannot overwrite a newer edit", () => {
  test("tab A saves; stale tab B is refused, keeps its draft, shows what is on the server, and chooses to discard", async ({ context }) => {
    const { a, b, invoice } = await twoTabs(context);

    await b.getByTestId("edit-details").click();
    await b.getByLabel("Description", { exact: true }).fill("From tab B");

    await a.getByTestId("edit-details").click();
    await a.getByLabel("Description", { exact: true }).fill("From tab A");
    await a.getByTestId("save-details").click();
    await expect(a.getByTestId("invoice-description")).toHaveText("From tab A");

    await b.getByTestId("save-details").click();
    const conflict = b.getByTestId("header-conflict");
    await expect(conflict).toBeVisible();
    await expect(conflict).toContainText("changed elsewhere");
    await expect(conflict).toContainText("From tab A"); // the authoritative state
    await expect(b.getByLabel("Description", { exact: true })).toHaveValue("From tab B"); // the draft is not destroyed
    await expect(b.getByTestId("save-details")).toBeDisabled();
    expect(description(invoice.id)).toBe("From tab A"); // nothing of B was saved

    await b.getByTestId("discard-details").click();
    await expect(b.getByTestId("invoice-description")).toHaveText("From tab A");
    await expect(b.getByTestId("save-details")).toHaveCount(0);
  });

  test("an open editor is not refreshed over, even when the tab becomes visible again", async ({ context }) => {
    const { a, b } = await twoTabs(context);
    await b.getByTestId("edit-details").click();
    await b.getByLabel("Description", { exact: true }).fill("Typing in B");
    await a.getByTestId("edit-details").click();
    await a.getByLabel("Description", { exact: true }).fill("A wins");
    await a.getByTestId("save-details").click();
    await expect(a.getByTestId("invoice-description")).toHaveText("A wins");

    await becomeVisible(b);
    await expect(b.getByLabel("Description", { exact: true })).toHaveValue("Typing in B");
  });

  test("a tab with nothing open picks up the newer version when it becomes visible", async ({ context }) => {
    const { a, b } = await twoTabs(context);
    await a.getByTestId("edit-details").click();
    await a.getByLabel("Description", { exact: true }).fill("Newer");
    await a.getByTestId("save-details").click();
    await expect(a.getByTestId("invoice-version")).toHaveText("2");

    await becomeVisible(b);
    await expect(b.getByTestId("invoice-description")).toHaveText("Newer");
    await expect(b.getByTestId("invoice-version")).toHaveText("2");
  });
});

test.describe("issuing from another tab", () => {
  test("a draft being edited in tab B is replaced by the issued invoice when B tries to save", async ({ context }) => {
    const { a, b, invoice } = await twoTabs(context);
    await b.getByTestId("edit-details").click();
    await b.getByLabel("Description", { exact: true }).fill("Too late");

    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();
    await expect(status(a)).toHaveAttribute("data-status", "issued");

    await b.getByTestId("save-details").click();
    await expect(status(b)).toHaveAttribute("data-status", "issued"); // the issued document replaced the stale draft UI
    await expect(b.getByTestId("invoice-notice")).toContainText("no longer a draft");
    await expect(b.getByTestId("invoice-number")).toHaveText("1");
    for (const control of ["issue", "delete-draft", "edit-details", "save-details"]) await expect(b.getByTestId(control)).toHaveCount(0);
    expect(description(invoice.id)).toBe("Original");
  });

  test("a tab with nothing open shows the issued invoice as soon as it becomes visible", async ({ context }) => {
    const { a, b } = await twoTabs(context);
    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();
    await expect(status(a)).toHaveAttribute("data-status", "issued");

    await becomeVisible(b);
    await expect(status(b)).toHaveAttribute("data-status", "issued");
    // The only control left is the PDF download: nothing that changes the invoice.
    await expect(b.getByTestId("invoice-view").getByRole("button")).toHaveCount(1);
    await expect(b.getByTestId("download-pdf")).toBeVisible();
  });

  test("both tabs press Issue: one number is allocated, and the other tab shows the issued invoice", async ({ context }) => {
    const { a, b, invoice } = await twoTabs(context);

    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();
    await expect(a.getByTestId("invoice-number")).toHaveText("1");

    await b.getByTestId("issue").click(); // B still believes it is a draft
    await b.getByTestId("issue-confirm").click();
    await expect(status(b)).toHaveAttribute("data-status", "issued");
    await expect(b.getByTestId("invoice-number")).toHaveText("1");
    await expect(b.getByTestId("invoice-notice")).toBeVisible();

    expect(testRow(`select count(*) from invoices where organization_id = ${sql(world.orgId)} and status = 'issued'`)).toBe("1");
    expect(testRow(`select next_number from invoice_counters where organization_id = ${sql(world.orgId)}`)).toBe("2"); // exactly one number was used
    expect((await getInvoiceApi(context, world.orgId, invoice.id)).number).toBe(1);
  });

  test("a draft deleted in another tab: the stale tab's Issue ends in the not-found state, without issuing anything", async ({ context }) => {
    const { a, b } = await twoTabs(context);
    await a.getByTestId("delete-draft").click();
    await a.getByTestId("delete-draft-confirm").click();
    await expect(a).toHaveURL(/invoices\?deleted=1/);

    await b.getByTestId("issue").click();
    await b.getByTestId("issue-confirm").click();
    // The screen explains it and is then re-read: a draft that is gone ends in the generic not-found page.
    await expect(b.getByTestId("not-found")).toBeVisible();
    expect(testRow(`select count(*) from invoice_counters where organization_id = ${sql(world.orgId)}`)).toBe("0");
  });

  test("a stale refusal of Issue is followed by the next attempt with the current version, only when the user chooses it", async ({ context }) => {
    const { a, invoice } = await twoTabs(context);
    // Someone else edits the draft through the API: this tab's version (1) is now stale.
    const edit = await context.request.patch(bffUrl(world.orgId, `/invoices/${invoice.id}`), { data: { description: "Edited elsewhere" }, headers: ifMatch(1) });
    expect(edit.status()).toBe(200);

    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();
    await expect(a.getByTestId("invoice-notice")).toContainText("changed elsewhere");
    await expect(a.getByTestId("invoice-version")).toHaveText("2");
    expect((await getInvoiceApi(context, world.orgId, invoice.id)).status).toBe("draft"); // not silently retried

    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();
    await expect(status(a)).toHaveAttribute("data-status", "issued");
  });
});

test.describe("a lost response is checked, never assumed", () => {
  test("the issuance COMMITTED but its answer was lost: the screen shows the issued invoice, and Issue is never sent twice", async ({ context }) => {
    const { a, invoice } = await twoTabs(context);
    let posts = 0;
    await a.route("**/api/o/*/invoices/*/issue", async (route) => {
      posts += 1;
      await route.fetch(); // the backend really issues the invoice ...
      await route.abort("failed"); // ... and the browser never gets the answer
    });

    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();

    await expect(status(a)).toHaveAttribute("data-status", "issued"); // found out by checking, not by assuming
    await expect(a.getByTestId("invoice-number")).toHaveText("1");
    await expect(a.getByTestId("issue")).toHaveCount(0);
    expect(posts).toBe(1);
    expect(testRow(`select status || '|' || number from invoices where id = ${sql(invoice.id)}`)).toBe("issued|1");
  });

  test("the request never reached the server: the check finds a draft, Issue is offered again, and only the user's next click issues", async ({ context }) => {
    const { a, invoice } = await twoTabs(context);
    let posts = 0;
    await a.route("**/api/o/*/invoices/*/issue", async (route) => {
      posts += 1;
      if (posts === 1) await route.abort("failed");
      else await route.continue();
    });

    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();

    await expect(a.getByTestId("invoice-notice")).toContainText("Checked");
    await expect(status(a)).toHaveText("Draft");
    expect(testRow(`select status from invoices where id = ${sql(invoice.id)}`)).toBe("draft");
    await expect(a.getByTestId("issue")).toBeEnabled();
    expect(posts).toBe(1); // nothing was retried on its own

    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();
    await expect(status(a)).toHaveAttribute("data-status", "issued");
    expect(posts).toBe(2);
    expect(testRow(`select next_number from invoice_counters where organization_id = ${sql(world.orgId)}`)).toBe("2");
  });

  test("while the check itself cannot be answered, no new attempt is offered", async ({ context }) => {
    const { a } = await twoTabs(context);
    await a.route("**/api/o/*/invoices/*/issue", (route) => route.abort("failed"));
    let failing = true;
    await a.route(/\/api\/o\/[^/]+\/invoices\/[0-9a-f-]{36}$/, (route) => (failing && route.request().method() === "GET" ? route.abort("failed") : route.continue()));

    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();

    await expect(a.getByTestId("check-again")).toBeVisible();
    await expect(a.getByTestId("issue")).toBeDisabled();
    failing = false;
    await a.getByTestId("check-again").click();
    await expect(a.getByTestId("issue")).toBeEnabled();
  });

  test("a deletion whose answer was lost is checked: the draft is gone, so the screen says so and does not navigate on a guess", async ({ context }) => {
    const { a } = await twoTabs(context);
    await a.route("**/api/o/*/invoices/*", async (route) => {
      if (route.request().method() !== "DELETE") return route.continue();
      await route.fetch();
      await route.abort("failed");
    });

    await a.getByTestId("delete-draft").click();
    await a.getByTestId("delete-draft-confirm").click();

    // The check finds the draft gone, says so, and the re-read page is the generic not-found page.
    await expect(a.getByTestId("not-found")).toBeVisible();
    await expect(a).not.toHaveURL(/deleted=1/); // it never navigated "on a guess"
    expect(testRow(`select count(*) from invoices where organization_id = ${sql(world.orgId)}`)).toBe("0");
  });
});

test.describe("one change at a time", () => {
  test("a change that is running keeps Issue and Delete disabled", async ({ context }) => {
    const { a } = await twoTabs(context);
    await a.route("**/api/o/*/invoices/*/issue", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue();
    });
    await a.getByTestId("issue").click();
    await a.getByTestId("issue-confirm").click();

    await expect(a.getByTestId("issue")).toBeDisabled();
    await expect(a.getByTestId("delete-draft")).toBeDisabled();
    await expect(status(a)).toHaveAttribute("data-status", "issued");
  });
});
