import { expect, test, type BrowserContext, type Page } from "./fixtures";

import {
  addLine,
  bffUrl,
  createCustomer,
  createTransaction,
  editLine,
  FREDRIK,
  getTransaction,
  ifMatch,
  lifecycle,
  openAddLine,
  ORG_A,
  ORG_B,
  signIn,
  sql,
  testRow,
  unique,
} from "./support";

/**
 * Optimistic concurrency as the BROWSER meets it: two tabs of one user (and API calls standing
 * in for "someone else"), against the real backend and the dedicated test database. A change
 * is refused when it was based on an old version, nothing changes on the server, and the
 * editor never throws the user's draft away.
 */

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

const url = (id: string) => `/o/${ORG_A.id}/transactions/${id}`;
const rows = (page: Page) => page.getByTestId("line-row");
const fieldOf = (page: Page, label: string) => page.getByLabel(label, { exact: true });

const LINE = { unit: "u", quantity: "1", unit_price_ex_vat: "10.00", vat_rate: "25" };

/** A draft with two lines, opened in two tabs. */
async function twoTabs(context: BrowserContext) {
  const customer = await createCustomer(context, ORG_A.id, unique("Concurrency Billing"));
  const transaction = await createTransaction(context, ORG_A.id, { billing_customer_id: customer.id, transaction_date: "2026-10-01" });
  const first = await addLine(context, ORG_A.id, transaction.id, { ...LINE, description: "Shared" });
  const other = await addLine(context, ORG_A.id, transaction.id, { ...LINE, description: "Other line" });
  const a = await context.newPage();
  const b = await context.newPage();
  await a.goto(url(transaction.id));
  await b.goto(url(transaction.id));
  await expect(rows(a)).toHaveCount(2);
  await expect(rows(b)).toHaveCount(2);
  return { a, b, transaction, first, other, customer };
}

const lineRow = (page: Page, text: string) => rows(page).filter({ hasText: text });

async function becomeVisible(page: Page) {
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

test.describe("a stale tab cannot overwrite a newer edit", () => {
  test("tab A saves a line; stale tab B is refused, keeps its draft, and chooses to discard", async ({ context }) => {
    const { a, b, transaction, first } = await twoTabs(context);

    // Tab B starts editing the line (and has not saved yet).
    await lineRow(b, "Shared").getByTestId("edit-line").click();
    await fieldOf(b, "Description").fill("From tab B");
    await fieldOf(b, "Quantity").fill("9");

    // Tab A edits the same line and saves first.
    await lineRow(a, "Shared").getByTestId("edit-line").click();
    await fieldOf(a, "Description").fill("From tab A");
    await fieldOf(a, "Quantity").fill("2");
    await a.getByTestId("save-line").click();
    await expect(lineRow(a, "From tab A")).toHaveCount(1);

    // Tab B saves: refused. The draft is not destroyed, and nothing refreshes over it.
    await b.getByTestId("save-line").click();
    await expect(b.getByTestId("line-conflict")).toContainText("This line was changed elsewhere. Your edits were not saved.");
    await expect(fieldOf(b, "Description")).toHaveValue("From tab B");
    await expect(fieldOf(b, "Quantity")).toHaveValue("9");
    await expect(b.getByTestId("save-line")).toBeDisabled();
    await expect(b.getByTestId("editor-notice")).toHaveCount(0);
    expect(testRow(`select description || '|' || quantity::text || '|' || version from transaction_lines where id = ${sql(first.id)}`)).toBe("From tab A|2.000|2");

    // The user chooses to discard; tab B now shows tab A's version and can save normally.
    await b.getByTestId("discard-line").click();
    await expect(b.getByTestId("line-editor")).toHaveCount(0);
    await expect(lineRow(b, "From tab A")).toHaveCount(1);
    await lineRow(b, "From tab A").getByTestId("edit-line").click();
    await fieldOf(b, "Description").fill("B after reload");
    await b.getByTestId("save-line").click();
    await expect(lineRow(b, "B after reload")).toHaveCount(1);
    expect(testRow(`select description || '|' || version from transaction_lines where id = ${sql(first.id)}`)).toBe("B after reload|3");
    expect((await getTransaction(context, ORG_A.id, transaction.id)).lines).toHaveLength(2);
  });

  test("when tab B refreshes for another reason, the open editor notices at once, with what is on the server now", async ({ context }) => {
    const { a, b, first, other } = await twoTabs(context);
    await lineRow(b, "Shared").getByTestId("edit-line").click();
    await fieldOf(b, "Description").fill("Half-written in B");
    await editLine(context, ORG_A.id, transaction(a), first.id, { description: "Changed by someone", quantity: "7" });
    void other;

    // Tab B does something else that refreshes the page (adds a line in its add-line form).
    await openAddLine(b);
    await b.getByLabel("Ad-hoc line").check();
    await b.getByTestId("add-line-form").getByLabel("Description", { exact: true }).fill("Added in B");
    await b.getByTestId("add-line-form").getByLabel("Unit", { exact: true }).fill("u");
    await b.getByTestId("add-line-form").getByLabel("Quantity", { exact: true }).fill("1");
    await b.getByTestId("add-line-form").getByLabel("Unit price excluding VAT", { exact: true }).fill("5.00");
    await b.getByTestId("add-line-form").getByLabel("VAT rate (%)", { exact: true }).fill("25");
    await b.getByTestId("submit-line").click();

    await expect(b.getByTestId("line-conflict")).toContainText("Changed by someone");
    await expect(b.getByTestId("line-conflict")).toContainText("7.000");
    await expect(b.getByTestId("line-editor").getByLabel("Description", { exact: true })).toHaveValue("Half-written in B");
    await expect(b.getByTestId("save-line")).toBeDisabled();
  });

  test("a stale header edit cannot overwrite a newer one", async ({ context }) => {
    const { a, b, transaction } = await twoTabs(context);
    await b.getByTestId("edit-header").click();
    await b.getByLabel("Date").fill("2031-05-05");

    await a.getByTestId("edit-header").click();
    await a.getByLabel("Date").fill("2026-11-11");
    await a.getByTestId("save-header").click();
    await expect(a.getByTestId("header-date")).toHaveText("2026-11-11");

    await b.getByTestId("save-header").click();
    await expect(b.getByTestId("header-conflict")).toContainText("The header was changed elsewhere. Your edits were not saved.");
    await expect(b.getByTestId("header-conflict")).toContainText("2026-11-11"); // what the server has now
    await expect(b.getByLabel("Date")).toHaveValue("2031-05-05"); // the draft survives
    expect(testRow(`select transaction_date::text || '|' || header_version from transactions where id = ${sql(transaction.id)}`)).toBe("2026-11-11|2");

    await b.getByTestId("discard-header").click();
    await expect(b.getByTestId("header-date")).toHaveText("2026-11-11");
  });

  test("a change to a line elsewhere does not make a header edit stale", async ({ context }) => {
    const { b, transaction, first } = await twoTabs(context);
    await b.getByTestId("edit-header").click();
    await b.getByLabel("Date").fill("2027-01-01");
    await editLine(context, ORG_A.id, transaction.id, first.id, { quantity: "4" });

    await b.getByTestId("save-header").click();

    await expect(b.getByTestId("header-date")).toHaveText("2027-01-01");
    await expect(b.getByTestId("header-conflict")).toHaveCount(0);
  });

  test("a stale delete cannot delete a line that was changed elsewhere", async ({ context }) => {
    const { b, transaction, first } = await twoTabs(context);
    await editLine(context, ORG_A.id, transaction.id, first.id, { description: "Edited elsewhere" });

    await lineRow(b, "Shared").getByTestId("delete-line").click();
    await lineRow(b, "Shared").getByTestId("delete-line-confirm").click();

    await expect(b.getByTestId("editor-notice")).toContainText("changed elsewhere, so nothing was changed");
    await expect(lineRow(b, "Edited elsewhere")).toHaveCount(1); // the latest is shown, and the line is still there
    expect(testRow(`select count(*) from transaction_lines where id = ${sql(first.id)}`)).toBe("1");

    // Having seen the latest, deleting works.
    await lineRow(b, "Edited elsewhere").getByTestId("delete-line").click();
    await lineRow(b, "Edited elsewhere").getByTestId("delete-line-confirm").click();
    await expect(rows(b)).toHaveCount(1);
    expect(testRow(`select count(*) from transaction_lines where id = ${sql(first.id)}`)).toBe("0");
  });

  test("a line deleted elsewhere: saving an edit of it says so and removes it from view", async ({ context }) => {
    const { b, transaction, first } = await twoTabs(context);
    await lineRow(b, "Shared").getByTestId("edit-line").click();
    await fieldOf(b, "Description").fill("Too late");
    const current = await getTransaction(context, ORG_A.id, transaction.id);
    const gone = await context.request.delete(bffUrl(ORG_A.id, `/transactions/${transaction.id}/lines/${first.id}`), { headers: ifMatch(current.lines.find((l) => l.id === first.id)!.version) });
    expect(gone.status()).toBe(204);

    await b.getByTestId("save-line").click();

    await expect(b.getByTestId("editor-notice")).toContainText("no longer exists");
    await expect(rows(b)).toHaveCount(1);
    await expect(b.getByTestId("line-editor")).toHaveCount(0);
  });
});

test.describe("stale lifecycle", () => {
  test("completing on a screen that missed a new line is refused, explained, and works after the refresh", async ({ context }) => {
    const { b, transaction } = await twoTabs(context);
    await addLine(context, ORG_A.id, transaction.id, { ...LINE, description: "Added elsewhere" });

    await b.getByTestId("invoice-order").click();

    await expect(b.getByTestId("editor-notice")).toContainText("changed elsewhere, so nothing was changed");
    expect(testRow(`select status from transactions where id = ${sql(transaction.id)}`)).toBe("draft");
    await expect(rows(b)).toHaveCount(3); // the screen now shows what FastAPI has
    await b.getByTestId("invoice-order").click();
    await expect(b.getByTestId("tx-status")).toHaveText("Completed");
  });

  test("completed in another tab: an open line editor can no longer save, says why, and the page becomes read-only", async ({ context }) => {
    const { b, transaction, first } = await twoTabs(context);
    await lineRow(b, "Shared").getByTestId("edit-line").click();
    await fieldOf(b, "Description").fill("Will not be saved");
    await lifecycle(context, ORG_A.id, transaction.id, "complete");

    await b.getByTestId("save-line").click();

    await expect(b.getByTestId("editor-notice")).toContainText("no longer a draft, so your changes could not be saved");
    await expect(b.getByTestId("tx-status")).toHaveText("Completed");
    await expect(b.getByTestId("line-editor")).toHaveCount(0);
    await expect(b.getByTestId("edit-line")).toHaveCount(0);
    expect(testRow(`select description from transaction_lines where id = ${sql(first.id)}`)).toBe("Shared");
  });

  test("completed in another tab: an open header editor is told the same, and a delete as well", async ({ context }) => {
    const { b, transaction } = await twoTabs(context);
    await b.getByTestId("edit-header").click();
    await b.getByLabel("Date").fill("2030-02-02");
    await lifecycle(context, ORG_A.id, transaction.id, "complete");

    await b.getByTestId("save-header").click();

    await expect(b.getByTestId("editor-notice")).toContainText("no longer a draft");
    await expect(b.getByTestId("tx-status")).toHaveText("Completed");
    expect(testRow(`select transaction_date::text from transactions where id = ${sql(transaction.id)}`)).toBe("2026-10-01");
  });

  test("completing a transaction that another tab already completed shows the state, not a crash", async ({ context }) => {
    const { b, transaction } = await twoTabs(context);
    await lifecycle(context, ORG_A.id, transaction.id, "complete");

    await b.getByTestId("invoice-order").click();

    await expect(b.getByTestId("editor-notice")).toContainText("completed order cannot be completed");
    await expect(b.getByTestId("tx-status")).toHaveText("Completed");
  });

  test("a stale Cancel is refused too, and the transaction is still a draft", async ({ context }) => {
    const { b, transaction } = await twoTabs(context);
    await addLine(context, ORG_A.id, transaction.id, { ...LINE, description: "Missed" });

    await b.getByTestId("cancel").click();
    await b.getByTestId("cancel-confirm").click();

    await expect(b.getByTestId("editor-notice")).toContainText("changed elsewhere");
    expect(testRow(`select status from transactions where id = ${sql(transaction.id)}`)).toBe("draft");
  });
});

test.describe("a tab that comes back", () => {
  test("refreshes by itself when nothing is being edited", async ({ context }) => {
    const { b, transaction } = await twoTabs(context);
    await addLine(context, ORG_A.id, transaction.id, { ...LINE, description: "Appears on its own" });
    await expect(rows(b)).toHaveCount(2); // the tab is stale until it is looked at again

    await expect(async () => {
      await becomeVisible(b); // retried until the page has finished loading and is listening
      await expect(rows(b)).toHaveCount(3, { timeout: 1500 });
    }).toPass({ timeout: 15_000 });
    await expect(lineRow(b, "Appears on its own")).toHaveCount(1);
  });

  test("never refreshes over an open editor, and does once the editor is closed", async ({ context }) => {
    const { b, transaction } = await twoTabs(context);
    await lineRow(b, "Shared").getByTestId("edit-line").click();
    await fieldOf(b, "Description").fill("My unsaved words");
    await addLine(context, ORG_A.id, transaction.id, { ...LINE, description: "Added meanwhile" });

    await becomeVisible(b);
    await b.waitForTimeout(1200);

    await expect(rows(b)).toHaveCount(1); // the other row is the one being edited; no refresh brought the new line
    await expect(lineRow(b, "Added meanwhile")).toHaveCount(0);
    await expect(fieldOf(b, "Description")).toHaveValue("My unsaved words");

    await b.getByTestId("cancel-line").click();
    await expect(b.getByTestId("line-editor")).toHaveCount(0);
    await expect(async () => {
      await becomeVisible(b); // retried until the page has noticed the editor is closed
      await expect(lineRow(b, "Added meanwhile")).toHaveCount(1, { timeout: 1000 });
    }).toPass({ timeout: 10_000 });
  });

  test("does not poll: a stale tab stays as it is until it is looked at", async ({ context }) => {
    const { b, transaction } = await twoTabs(context);
    await addLine(context, ORG_A.id, transaction.id, { ...LINE, description: "Unnoticed" });
    await b.waitForTimeout(3000);
    await expect(rows(b)).toHaveCount(2);
  });
});

test.describe("simultaneous saves", () => {
  test("two tabs saving the same line at the same moment: exactly one wins, the other is told", async ({ context }) => {
    const { a, b, first } = await twoTabs(context);
    for (const [tab, text] of [[a, "Simultaneous A"], [b, "Simultaneous B"]] as const) {
      await lineRow(tab, "Shared").getByTestId("edit-line").click();
      await fieldOf(tab, "Description").fill(text);
    }

    await Promise.all([a.getByTestId("save-line").click(), b.getByTestId("save-line").click()]);

    await expect.poll(() => testRow(`select version from transaction_lines where id = ${sql(first.id)}`)).toBe("2");
    const winner = testRow(`select description from transaction_lines where id = ${sql(first.id)}`);
    expect(["Simultaneous A", "Simultaneous B"]).toContain(winner);
    const losers = [a, b].filter((_, index) => (index === 0 ? "Simultaneous A" : "Simultaneous B") !== winner);
    await expect(losers[0].getByTestId("line-conflict")).toBeVisible();
    await expect(losers[0].getByTestId("line-editor").getByLabel("Description", { exact: true })).toHaveValue(winner === "Simultaneous A" ? "Simultaneous B" : "Simultaneous A");
  });
});

test.describe("through the BFF, with the version spelled out", () => {
  test("a stale request is a 409 stale_record that changes nothing; a fresh one works; the header is required", async ({ context }) => {
    const { transaction, first } = await twoTabs(context);
    const url = bffUrl(ORG_A.id, `/transactions/${transaction.id}/lines/${first.id}`);
    const before = await getTransaction(context, ORG_A.id, transaction.id);

    const missing = await context.request.patch(url, { data: { quantity: "5" } });
    const malformed = await context.request.patch(url, { data: { quantity: "5" }, headers: { "if-match": "banana" } });
    const wrong = await context.request.patch(url, { data: { quantity: "5" }, headers: ifMatch(99) });
    expect(missing.status()).toBe(428);
    expect(malformed.status()).toBe(400);
    expect(wrong.status()).toBe(409);
    expect((await wrong.json()).detail).toMatchObject({ code: "stale_record", entity_type: "transaction_line", entity_id: first.id, current_version: 1 });
    expect(await getTransaction(context, ORG_A.id, transaction.id)).toEqual(before);

    const fresh = await context.request.patch(url, { data: { quantity: "5" }, headers: ifMatch(1) });
    expect(fresh.status()).toBe(200);
    expect((await fresh.json()).version).toBe(2);
    const replay = await context.request.patch(url, { data: { quantity: "6" }, headers: ifMatch(1) }); // the same stale version again
    expect(replay.status()).toBe(409);
    expect((await getTransaction(context, ORG_A.id, transaction.id)).lines.find((l) => l.id === first.id)!.quantity).toBe("5.000");
  });
});

test.describe("a change that is still running when the user leaves", () => {
  test("a save in flight when the user switches organization still lands where it was started, and shows nothing in the other", async ({ context }) => {
    const { a, transaction, first } = await twoTabs(context);
    await a.route(`**/api/o/${ORG_A.id}/transactions/${transaction.id}/lines/${first.id}`, async (route) => {
      const response = await route.fetch(); // the request goes out at once...
      await new Promise((resolve) => setTimeout(resolve, 1500)); // ...the answer is slow
      await route.fulfill({ response }).catch(() => {});
    });
    await lineRow(a, "Shared").getByTestId("edit-line").click();
    await fieldOf(a, "Description").fill("Saved while leaving");
    await a.getByTestId("save-line").click();

    await a.getByTestId("org-switcher").getByRole("link", { name: ORG_B.name }).click();
    await expect(a).toHaveURL(`/o/${ORG_B.id}`);

    await expect.poll(() => testRow(`select description from transaction_lines where id = ${sql(first.id)}`)).toBe("Saved while leaving");
    await a.waitForTimeout(1800);
    await expect(a.getByTestId("org-name")).toHaveText(ORG_B.name);
    await expect(a.getByTestId("editor-notice")).toHaveCount(0);
    expect(await a.locator("body").innerText()).not.toContain("Saved while leaving");
  });
});

// the transaction id of the page a tab shows (a tiny helper kept at the bottom)
function transaction(page: Page): string {
  return page.url().match(/transactions\/([0-9a-f-]{36})/)![1];
}
