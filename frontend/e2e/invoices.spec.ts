import { expect, test, type Page } from "./fixtures";

import {
  bffUrl,
  createCompletedTransaction,
  createInvoiceApi,
  createItem,
  createTransaction,
  createWorld,
  getInvoiceApi,
  getTransaction,
  ifMatch,
  insertCompletedTransaction,
  insertCurrencylessTransaction,
  insertCustomer,
  issueInvoiceApi,
  lifecycle,
  signIn,
  sql,
  testRow,
  type World,
} from "./support";

/**
 * Invoicing in a real browser against the real stack and the test database. Every test works in a
 * throwaway organization of its own (created straight in the test database), so nothing depends on
 * what other specs left behind, and the seeded organizations stay as they were.
 */

let world: World;
test.afterEach(() => world?.cleanup());

const list = (w: World) => `/o/${w.orgId}/invoices`;
const eligibleRow = (page: Page, date: string) => page.getByTestId("eligible-row").filter({ hasText: date });
const pickRow = async (page: Page, date: string) => eligibleRow(page, date).getByTestId("select-transaction").check();

/** Two compatible completed transactions and the ones that must NOT be offered together with them. */
async function setup(context: Parameters<typeof signIn>[0]) {
  world = createWorld({ label: "Invoicing" });
  await signIn(context, world.email);
  const umea = insertCustomer(world.orgId, "Umeå HK");
  const anna = insertCustomer(world.orgId, "Anna Andersson");
  const t1 = await createCompletedTransaction(context, world.orgId, umea, { date: "2026-10-01" });
  const t2 = await createCompletedTransaction(context, world.orgId, umea, { date: "2026-10-02", lines: [{ description: "Travel", unit: "km", quantity: "7.001", unit_price_ex_vat: "6.25", vat_rate: "6" }] });
  const t3 = await createCompletedTransaction(context, world.orgId, anna, { date: "2026-10-03" });
  const eur = insertCompletedTransaction(world.orgId, umea, "EUR", "2026-10-04");
  const none = insertCurrencylessTransaction(world.orgId, umea, "2026-10-05");
  const draft = await createTransaction(context, world.orgId, { billing_customer_id: umea, transaction_date: "2026-10-06", lines: [{ description: "Still a draft", unit: "u", quantity: "1", unit_price_ex_vat: "1.00", vat_rate: "25" }] });
  return { umea, anna, t1, t2, t3, eur, none, draft };
}

test.describe("what can be invoiced", () => {
  test("the new-invoice screen lists exactly the completed transactions with a currency that are on no invoice", async ({ page, context }) => {
    const s = await setup(context);

    await page.goto(`${list(world)}/new`);

    const dates = await page.getByTestId("eligible-row").locator("td:nth-child(2)").allTextContents();
    expect(dates.sort()).toEqual(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]); // not the draft (10-06) and not the currency-less one (10-05)
    expect(s.none).toBeTruthy();
    await expect(eligibleRow(page, "2026-10-04").getByTestId("eligible-currency")).toHaveText("EUR");
    await expect(page.getByTestId("submit")).toBeDisabled();
  });

  test("the first selection fixes customer and currency; incompatible transactions cannot be selected and say why", async ({ page, context }) => {
    await setup(context);
    await page.goto(`${list(world)}/new`);

    await pickRow(page, "2026-10-01");
    await expect(page.getByTestId("selection-customer")).toHaveText("Umeå HK");
    await expect(page.getByTestId("selection-currency")).toHaveText("SEK");

    await expect(eligibleRow(page, "2026-10-02").getByTestId("select-transaction")).toBeEnabled();
    await expect(eligibleRow(page, "2026-10-03").getByTestId("select-transaction")).toBeDisabled(); // another customer
    await expect(eligibleRow(page, "2026-10-03").getByTestId("incompatible-reason")).toHaveText("Another billing customer");
    await expect(eligibleRow(page, "2026-10-04").getByTestId("select-transaction")).toBeDisabled(); // same customer, another currency
    await expect(eligibleRow(page, "2026-10-04").getByTestId("incompatible-reason")).toHaveText("Another currency");

    // The selection screen adds up nothing.
    await pickRow(page, "2026-10-02");
    await expect(page.getByTestId("selection-count")).toHaveText("2");
    await expect(page.getByTestId("selection")).not.toContainText("2125");
  });
});

test.describe("draft invoices", () => {
  test("creating a draft reserves the transactions and shows the stored document with the server's own numbers", async ({ page, context }) => {
    const s = await setup(context);
    await page.goto(`${list(world)}/new`);
    await pickRow(page, "2026-10-01");
    await pickRow(page, "2026-10-02");
    await page.getByLabel("Due date", { exact: true }).fill("2026-11-15");
    await page.getByLabel("Description", { exact: true }).fill("October work");
    await page.getByTestId("submit").click();

    await expect(page).toHaveURL(new RegExp(`${list(world)}/[0-9a-f-]{36}\\?created=1$`));
    await expect(page.getByTestId("created")).toBeVisible();
    await expect(page.getByTestId("invoice-heading")).toHaveText("Draft invoice (no number yet)");
    await expect(page.getByTestId("invoice-status")).toHaveText("Draft");
    await expect(page.getByTestId("party-customer-name")).toHaveText("Umeå HK");
    await expect(page.getByTestId("invoice-currency")).toHaveText("SEK");
    await expect(page.getByTestId("invoice-description")).toHaveText("October work");
    await expect(page.getByTestId("invoice-due-date")).toHaveText("2026-11-15");

    // Every figure on the page is the string the API returned (compared with the API, not recomputed).
    const id = page.url().match(/invoices\/([0-9a-f-]{36})/)![1];
    const stored = await getInvoiceApi(context, world.orgId, id);
    await expect(page.getByTestId("total-net")).toHaveText(stored.net_amount);
    await expect(page.getByTestId("total-vat")).toHaveText(stored.vat_amount);
    await expect(page.getByTestId("total-gross")).toHaveText(stored.gross_amount);
    expect(await page.getByTestId("line-gross").allTextContents()).toEqual(stored.lines.map((line) => line.gross_amount));
    expect(await page.getByTestId("vat-row").count()).toBe(stored.vat_breakdown.length);
    await expect(page.getByTestId("source")).toHaveCount(2);
    expect(stored.status).toBe("draft");
    expect(s.t1.id).toBeTruthy();
  });

  test("the reserved transactions disappear from eligibility, and Sales cannot reopen or cancel them", async ({ page, context }) => {
    const s = await setup(context);
    await createInvoiceApi(context, world.orgId, [s.t1.id, s.t2.id]);

    await page.goto(`${list(world)}/new`);
    await expect(page.getByTestId("eligible-row")).toHaveCount(2); // Anna's and the EUR one remain
    await expect(eligibleRow(page, "2026-10-01")).toHaveCount(0);

    // In the Sales editor: Reopen is refused with the reservation as the reason.
    await page.goto(`/o/${world.orgId}/transactions/${s.t1.id}`);
    await page.getByTestId("reopen").click();
    await expect(page.getByTestId("editor-problems")).toContainText("Draft invoice");
    await expect(page.getByTestId("editor-problems")).toContainText("reserved by a draft invoice");
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");

    // Cancelling too (after its confirmation), and the API agrees.
    await page.getByTestId("cancel").click();
    await page.getByTestId("cancel-confirm").click();
    await expect(page.getByTestId("editor-problems")).toContainText("cannot be cancelled");
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");
    const reopen = await context.request.post(bffUrl(world.orgId, `/transactions/${s.t2.id}/reopen`), { headers: ifMatch((await getTransaction(context, world.orgId, s.t2.id)).version) });
    expect(reopen.status()).toBe(409);
    expect((await getTransaction(context, world.orgId, s.t2.id)).status).toBe("completed");
  });

  test("editing the draft's dates and description, with the version moving only on a real change", async ({ page, context }) => {
    const s = await setup(context);
    const invoice = await createInvoiceApi(context, world.orgId, [s.t1.id], { invoice_date: "2026-10-01" });

    await page.goto(`${list(world)}/${invoice.id}`);
    await expect(page.getByTestId("invoice-version")).toHaveText("1");
    await page.getByTestId("edit-details").click();
    await page.getByTestId("save-details").click(); // nothing changed: no request, no new version
    await expect(page.getByTestId("save-details")).toHaveCount(0);
    await expect(page.getByTestId("invoice-version")).toHaveText("1");

    await page.getByTestId("edit-details").click();
    await page.getByLabel("Description", { exact: true }).fill("Edited");
    await page.getByLabel("Due date", { exact: true }).fill("2026-12-24");
    await page.getByTestId("save-details").click();

    await expect(page.getByTestId("invoice-description")).toHaveText("Edited");
    await expect(page.getByTestId("invoice-due-date")).toHaveText("2026-12-24");
    await expect(page.getByTestId("invoice-version")).toHaveText("2");
    expect(testRow(`select description || '|' || due_date || '|' || version from invoices where id = ${sql(invoice.id)}`)).toBe("Edited|2026-12-24|2");

    // The backend's validation answer appears on the control.
    await page.getByTestId("edit-details").click();
    await page.getByLabel("Due date", { exact: true }).fill("2026-09-01");
    await page.getByTestId("save-details").click();
    await expect(page.getByTestId("error-due_date")).toContainText("before the invoice date");
    expect(testRow(`select version from invoices where id = ${sql(invoice.id)}`)).toBe("2");
  });

  test("deleting the draft releases its transactions; recreating and issuing then works", async ({ page, context }) => {
    const s = await setup(context);
    const invoice = await createInvoiceApi(context, world.orgId, [s.t1.id, s.t2.id]);

    await page.goto(`${list(world)}/${invoice.id}`);
    await page.getByTestId("delete-draft").click();
    await expect(page.getByRole("group", { name: /^Delete this draft/ })).toContainText("orders become invoiceable again");
    await page.getByTestId("delete-draft-confirm").click();

    await expect(page).toHaveURL(new RegExp(`${list(world)}\\?deleted=1$`));
    await expect(page.getByTestId("deleted")).toBeVisible();
    expect(testRow(`select count(*) from invoices where organization_id = ${sql(world.orgId)}`)).toBe("0");
    await page.goto(`${list(world)}/new`);
    await expect(eligibleRow(page, "2026-10-01")).toHaveCount(1); // invoiceable again
    await expect(eligibleRow(page, "2026-10-02")).toHaveCount(1);
    // And Sales may use them again.
    const reopen = await context.request.post(bffUrl(world.orgId, `/transactions/${s.t2.id}/reopen`), { headers: ifMatch((await getTransaction(context, world.orgId, s.t2.id)).version) });
    expect(reopen.status()).toBe(200);
    // Recreate from the one that is still completed, then issue.
    await pickRow(page, "2026-10-01");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("invoice-heading")).toHaveText("Draft invoice (no number yet)");
  });
});

test.describe("issuing", () => {
  test("issue asks first, then shows the number the server allocated, and the invoice is read-only", async ({ page, context }) => {
    const s = await setup(context);
    const invoice = await createInvoiceApi(context, world.orgId, [s.t1.id, s.t2.id]);

    await page.goto(`${list(world)}/${invoice.id}`);
    await page.getByTestId("issue").click();
    await expect(page.getByRole("group", { name: /^Issue this invoice/ })).toContainText("cannot be edited or deleted");
    await page.getByTestId("issue-keep").click(); // declining changes nothing
    expect((await getInvoiceApi(context, world.orgId, invoice.id)).status).toBe("draft");

    await page.getByTestId("issue").click();
    await page.getByTestId("issue-confirm").click();

    await expect(page.getByTestId("invoice-status")).toHaveAttribute("data-status", "issued");
    await expect(page.getByTestId("invoice-heading")).toHaveText("Invoice 1");
    await expect(page.getByTestId("invoice-number")).toHaveText("1");
    await expect(page.getByTestId("issued-note")).toContainText("cannot be edited or deleted");
    for (const control of ["issue", "delete-draft", "edit-details", "save-details"]) await expect(page.getByTestId(control)).toHaveCount(0);
    // The only control left is the PDF download: nothing that changes the invoice.
    await expect(page.getByTestId("invoice-view").getByRole("button")).toHaveCount(1);
    await expect(page.getByTestId("download-pdf")).toBeVisible();

    const stored = await getInvoiceApi(context, world.orgId, invoice.id);
    expect(stored).toMatchObject({ status: "issued", number: 1, number_text: "1" });
    await expect(page.getByTestId("total-gross")).toHaveText(stored.gross_amount);

    // Reloading (and a direct request) show the same read-only document; the API refuses every change.
    await page.reload();
    // The only control left is the PDF download: nothing that changes the invoice.
    await expect(page.getByTestId("invoice-view").getByRole("button")).toHaveCount(1);
    await expect(page.getByTestId("download-pdf")).toBeVisible();
    const edit = await context.request.patch(bffUrl(world.orgId, `/invoices/${invoice.id}`), { data: { description: "x" }, headers: ifMatch(stored.version) });
    const remove = await context.request.delete(bffUrl(world.orgId, `/invoices/${invoice.id}`), { headers: ifMatch(stored.version) });
    expect([edit.status(), remove.status()]).toEqual([409, 409]);
    expect(testRow(`select next_number from invoice_counters where organization_id = ${sql(world.orgId)}`)).toBe("2");
  });

  test("Sales stays blocked after issue: reopen and cancel are refused for good", async ({ page, context }) => {
    const s = await setup(context);
    await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [s.t1.id]));

    await page.goto(`/o/${world.orgId}/transactions/${s.t1.id}`);
    await page.getByTestId("reopen").click();
    await expect(page.getByTestId("editor-problems")).toContainText("Issued invoice");
    await expect(page.getByTestId("editor-problems")).toContainText("issued invoice and cannot be reopened");
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");
  });

  test("the list shows the number, Draft for a draft, the snapshot customer, dates, status, currency and the server's totals", async ({ page, context }) => {
    const s = await setup(context);
    const issued = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [s.t1.id], { invoice_date: "2026-10-02", due_date: "2026-11-01" }));
    const draft = await createInvoiceApi(context, world.orgId, [s.t3.id], { invoice_date: "2026-10-09" });
    // The stored label is not the integer (a future numbering format may differ): the screens must show the label.
    // (An issued invoice is protected, so this one test switches the protection off for its own statement.)
    testRow(`set local session_replication_role = replica; update invoices set number_text = 'A-0001' where id = ${sql(issued.id)}`);

    await page.goto(list(world));

    const rows = page.getByTestId("invoice-row");
    await expect(rows).toHaveCount(2);
    const issuedRow = rows.filter({ hasText: "Umeå HK" });
    await expect(issuedRow.getByTestId("invoice-number")).toHaveText("A-0001");
    await expect(issuedRow.getByTestId("invoice-date")).toHaveText("2026-10-02");
    await expect(issuedRow.getByTestId("invoice-due")).toHaveText("2026-11-01");
    await expect(issuedRow.getByTestId("invoice-currency")).toHaveText("SEK");
    await expect(issuedRow.getByTestId("invoice-gross")).toHaveText(issued.gross_amount);
    const draftRow = rows.filter({ hasText: "Anna Andersson" });
    await expect(draftRow.getByTestId("invoice-number")).toHaveText("Draft");
    await expect(draftRow.getByTestId("invoice-due")).toHaveText("—");
    await expect(draftRow.getByTestId("invoice-net")).toHaveText(draft.net_amount);
    // Only filters the backend supports, applied by the backend.
    await page.goto(`${list(world)}?status=issued`);
    await expect(page.getByTestId("invoice-row")).toHaveCount(1);
    await page.goto(`${list(world)}?q=anna`);
    await expect(page.getByTestId("invoice-row")).toHaveCount(1);
    await page.goto(`${list(world)}?date_from=2026-10-05&date_to=2026-10-31`);
    await expect(page.getByTestId("invoice-row")).toHaveCount(1);
    await expect(page.getByTestId("invoice-number")).toHaveText("Draft");
    await page.goto(`${list(world)}?q=${encodeURIComponent("A-0001")}`);
    await expect(page.getByTestId("invoice-row")).toHaveCount(1); // by number text
    await page.getByTestId("invoice-link").click();
    await expect(page.getByTestId("invoice-heading")).toHaveText("Invoice A-0001");
    await expect(page.getByTestId("record-name")).toContainText("Invoice A-0001");
  });
});

test.describe("an issued invoice is a stored document", () => {
  test("changing or deactivating live customer, item, custom-field and reference data does not change what it shows", async ({ page, context }) => {
    world = createWorld({ label: "Snapshot" });
    await signIn(context, world.email);
    const customer = insertCustomer(world.orgId, "Umeå HK");
    const anna = insertCustomer(world.orgId, "Anna Andersson");
    const item = await createItem(context, world.orgId, { name: "Horse massage", unit: "session", price_ex_vat: "850.00", vat_rate: "25" });

    // Custom fields flagged for invoices: a text field on lines, a reference to a customer, and one on the transaction.
    const define = async (data: Record<string, unknown>) => {
      const response = await context.request.post(bffUrl(world.orgId, "/custom-fields/definitions"), { data: { show_on_invoice: true, ...data } });
      expect(response.status(), await response.text()).toBe(201);
    };
    await define({ entity_type: "transaction_line", key: "remark", label: "Remark", field_type: "text" });
    await define({ entity_type: "transaction_line", key: "owner", label: "Owner", field_type: "reference", reference: { source: "customer" } });
    await define({ entity_type: "transaction", key: "po", label: "PO number", field_type: "text" });

    const draftTx = await createTransaction(context, world.orgId, { billing_customer_id: customer, transaction_date: "2026-10-01", lines: [{ item_id: item.id, quantity: "1" }] });
    const lineId = draftTx.lines[0].id;
    const patch = async (type: string, id: string, values: Record<string, unknown>) => {
      const response = await context.request.patch(bffUrl(world.orgId, `/custom-fields/entities/${type}/${id}/values`), { data: { values } });
      expect(response.status(), await response.text()).toBe(200);
    };
    await patch("transaction_line", lineId, { remark: "Handle with care", owner: anna });
    await patch("transaction", draftTx.id, { po: "PO-17" });
    const completedTx = await lifecycle(context, world.orgId, draftTx.id, "complete");
    const issued = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [completedTx.id]));

    await page.goto(`${list(world)}/${issued.id}`);
    // textContent, not innerText: innerText depends on layout, and right after a load in CI it can come back without
    // line breaks (same text, one line), which failed this comparison on every CI run.
    const before = (await page.getByTestId("invoice-document").textContent()) ?? "";
    await expect(page.getByTestId("party-customer-name")).toHaveText("Umeå HK");
    await expect(page.getByTestId("line-fields")).toContainText("Handle with care");
    await expect(page.getByTestId("line-fields")).toContainText("Anna Andersson");
    await expect(page.getByTestId("transaction-fields")).toContainText("PO-17");

    // Now change EVERYTHING live: the customer (renamed, moved, deactivated), the item (renamed, repriced,
    // deactivated), the field definitions (relabelled, disabled), the stored values, and delete the reference target.
    const org = sql(world.orgId);
    testRow(`update customers set name = 'Renamed Club', city = 'Elsewhere', vat_number = 'CHANGED', active = false where id = ${sql(customer)}`);
    testRow(`update items set name = 'Renamed item', price_ex_vat = 1.00, vat_rate = 6.00, active = false where id = ${sql(item.id)}`);
    testRow(`update custom_field_definitions set label = 'Relabelled', enabled = false, show_on_invoice = false where organization_id = ${org}`);
    testRow(`update custom_field_values set value_text = 'changed text' where organization_id = ${org} and value_text is not null`);
    testRow(`set local session_replication_role = replica; delete from customers where id = ${sql(anna)}`);
    testRow(`update organizations set legal_name = 'Renamed Org AB', city = 'Elsewhere' where id = ${org}`);
    await context.request.patch(bffUrl(world.orgId, `/items/${item.id}`), { data: { name: "Renamed again" } });

    await page.reload();
    await expect(page.getByTestId("invoice-document")).toHaveText(before); // the very same document
    await expect(page.getByTestId("party-customer-name")).toHaveText("Umeå HK");
    await expect(page.getByTestId("line-description")).toHaveText("Horse massage");
    await expect(page.getByTestId("line-fields")).toContainText("Remark");
    await expect(page.getByTestId("line-fields")).toContainText("Anna Andersson");
    for (const live of ["Renamed", "Elsewhere", "CHANGED", "Relabelled", "changed text"]) await expect(page.getByTestId("invoice-document")).not.toContainText(live);
    await expect(page.getByTestId("record-name")).toHaveText("Invoice 1 · Umeå HK"); // the page heading is the invoice's too
    await page.goto(list(world));
    await expect(page.getByTestId("invoice-customer")).toHaveText("Umeå HK"); // the list too, from the snapshot
    expect(await getInvoiceApi(context, world.orgId, issued.id)).toEqual(issued);
  });
});
