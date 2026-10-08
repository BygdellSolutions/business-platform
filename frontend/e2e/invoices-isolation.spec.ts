import { expect, test } from "./fixtures";

import { bffUrl, createCompletedTransaction, createInvoiceApi, createWorld, ifMatch, insertCustomer, issueInvoiceApi, signIn, sql, testRow, type InvoiceJson, type World } from "./support";

/**
 * Tenant isolation for invoices in a real browser: two organizations whose records look
 * identical (same customer name, same lines, same dates, and invoice number 1 in each), and a
 * user who must never be able to see, change, reference or even detect the other one's.
 */

const RANDOM = "00000000-0000-4000-8000-00000000beef";
let a: World;
let b: World;
test.afterEach(() => {
  a?.cleanup();
  b?.cleanup();
});

/** Organizations A and B, each with an issued invoice number 1 and a draft, built from identical-looking data. */
async function build(context: Parameters<typeof signIn>[0]) {
  a = createWorld({ label: "IsoA" });
  b = createWorld({ label: "IsoB" });
  const made: Record<"a" | "b", { world: World; customer: string; issued: InvoiceJson; draft: InvoiceJson; free: string }> = {} as never;
  for (const [key, world] of [["a", a], ["b", b]] as const) {
    await signIn(context, world.email);
    const customer = insertCustomer(world.orgId, "Anna Andersson"); // the same name in both organizations
    const t1 = await createCompletedTransaction(context, world.orgId, customer, { date: "2026-10-01" });
    const t2 = await createCompletedTransaction(context, world.orgId, customer, { date: "2026-10-02" });
    const t3 = await createCompletedTransaction(context, world.orgId, customer, { date: "2026-10-03" });
    const issued = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [t1.id], { invoice_date: "2026-10-05" }));
    const draft = await createInvoiceApi(context, world.orgId, [t2.id], { invoice_date: "2026-10-06" });
    made[key] = { world, customer, issued, draft, free: t3.id };
  }
  await signIn(context, a.email); // the browser is A's user from here on
  return made;
}

test("identical invoice numbers exist independently in both organizations", async ({ page, context }) => {
  const m = await build(context);
  expect(m.a.issued.number_text).toBe("1");
  expect(m.b.issued.number_text).toBe("1");
  expect(m.a.issued.id).not.toBe(m.b.issued.id);
  expect(testRow(`select string_agg(organization_id::text || ':' || next_number, ',' order by organization_id) from invoice_counters where organization_id in (${sql(a.orgId)}, ${sql(b.orgId)})`).split(",").map((part) => part.split(":")[1])).toEqual(["2", "2"]);

  await page.goto(`/o/${a.orgId}/invoices`);
  const rows = page.getByTestId("invoice-row");
  await expect(rows).toHaveCount(2); // only A's two, although B has two identical-looking ones
  await expect(rows.filter({ hasText: "Anna Andersson" })).toHaveCount(2);
  await expect(page.getByTestId("invoice-number").first()).toBeVisible();
  const hrefs = await page.getByTestId("invoice-link").evaluateAll((links) => links.map((link) => (link as HTMLAnchorElement).getAttribute("href")));
  expect(hrefs.sort()).toEqual([`/o/${a.orgId}/invoices/${m.a.draft.id}`, `/o/${a.orgId}/invoices/${m.a.issued.id}`].sort());
  expect(hrefs.join(" ")).not.toContain(m.b.issued.id);
});

test("a foreign invoice id is the same not-found page and the same API answer as a random one", async ({ page, context }) => {
  const m = await build(context);

  await page.goto(`/o/${a.orgId}/invoices/${m.b.issued.id}`);
  await expect(page.getByTestId("not-found")).toBeVisible();
  const foreignText = await page.locator("main").innerText();
  await page.goto(`/o/${a.orgId}/invoices/${RANDOM}`);
  await expect(page.getByTestId("not-found")).toBeVisible();
  expect(await page.locator("main").innerText()).toBe(foreignText); // indistinguishable
  await expect(page.getByText("Invoice 1")).toHaveCount(0);

  const verbs = (id: string) => [
    () => context.request.get(bffUrl(a.orgId, `/invoices/${id}`)),
    () => context.request.patch(bffUrl(a.orgId, `/invoices/${id}`), { data: { description: "Forged" }, headers: ifMatch(1) }),
    () => context.request.patch(bffUrl(a.orgId, `/invoices/${id}`), { data: { description: "Forged" } }),
    () => context.request.post(bffUrl(a.orgId, `/invoices/${id}/issue`), { headers: ifMatch(1) }),
    () => context.request.post(bffUrl(a.orgId, `/invoices/${id}/issue`), { headers: ifMatch(99) }),
    () => context.request.delete(bffUrl(a.orgId, `/invoices/${id}`), { headers: ifMatch(1) }),
    () => context.request.delete(bffUrl(a.orgId, `/invoices/${id}`)),
  ];
  const foreign = await Promise.all(verbs(m.b.draft.id).map((call) => call()));
  const random = await Promise.all(verbs(RANDOM).map((call) => call()));
  expect(foreign.map((r) => r.status())).toEqual(random.map((r) => r.status()));
  expect(foreign.every((r) => r.status() === 404)).toBe(true);
  expect(await Promise.all(foreign.map((r) => r.text()))).toEqual(await Promise.all(random.map((r) => r.text())));
  expect(testRow(`select status || '|' || version from invoices where id = ${sql(m.b.draft.id)}`)).toBe("draft|1"); // untouched
});

test("foreign transaction ids cannot be put on an invoice: the answer is the same as for random ids, and nothing is reserved", async ({ context }) => {
  const m = await build(context);
  const attempt = (ids: string[]) => context.request.post(bffUrl(a.orgId, "/invoices"), { data: { transaction_ids: ids } });

  const foreign = await attempt([m.b.free]);
  const random = await attempt([RANDOM]);
  const mine = await attempt([m.a.free, m.b.free]); // one of mine, one of theirs
  const mineRandom = await attempt([m.a.free, RANDOM]);

  for (const response of [foreign, random, mine, mineRandom]) expect(response.status()).toBe(422);
  const bodies = await Promise.all([foreign, random, mine, mineRandom].map((response) => response.text()));
  expect(new Set(bodies).size).toBe(1); // byte-for-byte the same body: nothing says which id was the problem
  expect(bodies[0]).not.toContain(m.b.free);
  expect(testRow(`select count(*) from invoice_transactions where transaction_id in (${sql(m.b.free)}, ${sql(m.a.free)})`)).toBe("0");
});

test("eligibility, the invoice-state view and the pages show only the active organization's records", async ({ page, context }) => {
  const m = await build(context);

  await page.goto(`/o/${a.orgId}/invoices/new`);
  await expect(page.getByTestId("eligible-row")).toHaveCount(1); // A's free transaction only
  const links = await page.getByTestId("eligible-row").locator("a").evaluateAll((anchors) => anchors.map((anchor) => (anchor as HTMLAnchorElement).getAttribute("href")));
  expect(links).toEqual([`/o/${a.orgId}/transactions/${m.a.free}`]);

  const state = await context.request.get(bffUrl(a.orgId, `/invoices/by-transaction?ids=${m.b.free},${m.a.free}`));
  expect(await state.json()).toEqual([
    { transaction_id: m.b.free, state: "none", invoice_id: null, number_text: null }, // a foreign id looks exactly like a free one
    { transaction_id: m.a.free, state: "none", invoice_id: null, number_text: null },
  ]);
  const eligible = (await (await context.request.get(bffUrl(a.orgId, "/invoiceable-transactions"))).json()) as { id: string }[];
  expect(eligible.map((row) => row.id)).toEqual([m.a.free]);
});

test("switching organization clears the invoice list, the detail and an open draft editor", async ({ page, context }) => {
  const m = await build(context);
  // One person who belongs to both organizations.
  testRow(`update users set max_owned_organizations = max_owned_organizations + 1 where email = ${sql(a.email)}; insert into organization_users (organization_id, user_id, role) select ${sql(b.orgId)}, id, 'owner' from users where email = ${sql(a.email)}`); // owning a second organization needs the allowance

  await page.goto(`/o/${a.orgId}/invoices/${m.a.draft.id}`);
  await page.getByTestId("edit-details").click();
  await page.getByLabel("Description", { exact: true }).fill("A's unsaved draft text");

  // Switch organization (the switcher is a plain link: a full navigation, so no client state can carry over).
  await page.goto(`/o/${b.orgId}/invoices`);
  await expect(page.getByTestId("invoice-row")).toHaveCount(2);
  await expect(page.getByText("A's unsaved draft text")).toHaveCount(0);
  const hrefs = await page.getByTestId("invoice-link").evaluateAll((links) => links.map((link) => (link as HTMLAnchorElement).getAttribute("href")));
  expect(hrefs.join(" ")).not.toContain(m.a.draft.id);
  expect(hrefs.join(" ")).not.toContain(m.a.issued.id);

  await page.goto(`/o/${b.orgId}/invoices/${m.b.draft.id}`);
  await expect(page.getByLabel("Description", { exact: true })).toHaveCount(0); // no editor carried over
  await expect(page.getByTestId("invoice-description")).not.toHaveText("A's unsaved draft text");

  // And back: nothing of B leaked into A, and A's draft was never saved.
  await page.goto(`/o/${a.orgId}/invoices/${m.a.draft.id}`);
  await expect(page.getByTestId("invoice-description")).not.toHaveText("A's unsaved draft text");
  expect(testRow(`select coalesce(description, '-') from invoices where id = ${sql(m.a.draft.id)}`)).toBe("-");
});

test("a request for one organization's invoice through the other organization's address is refused for a member of both", async ({ context }) => {
  const m = await build(context);
  testRow(`update users set max_owned_organizations = max_owned_organizations + 1 where email = ${sql(a.email)}; insert into organization_users (organization_id, user_id, role) select ${sql(b.orgId)}, id, 'owner' from users where email = ${sql(a.email)}`); // owning a second organization needs the allowance

  // Member of both, but the invoice belongs to B: through A's address it does not exist.
  const viaA = await context.request.get(bffUrl(a.orgId, `/invoices/${m.b.issued.id}`));
  const viaB = await context.request.get(bffUrl(b.orgId, `/invoices/${m.b.issued.id}`));
  expect(viaA.status()).toBe(404);
  expect(viaB.status()).toBe(200);
  expect(((await viaB.json()) as InvoiceJson).number_text).toBe("1");
});

test("a request that is still running when the user switches organization cannot touch the other organization's screen", async ({ page, context }) => {
  const m = await build(context);
  testRow(`update users set max_owned_organizations = max_owned_organizations + 1 where email = ${sql(a.email)}; insert into organization_users (organization_id, user_id, role) select ${sql(b.orgId)}, id, 'owner' from users where email = ${sql(a.email)}`); // owning a second organization needs the allowance
  await page.route("**/api/o/*/invoices/*/issue", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500)); // a slow answer for A's issuance
    await route.continue();
  });

  await page.goto(`/o/${a.orgId}/invoices/${m.a.draft.id}`);
  await page.getByTestId("issue").click();
  await page.getByTestId("issue-confirm").click();
  await page.goto(`/o/${b.orgId}/invoices`); // switch while it runs

  await expect(page.getByTestId("invoice-row")).toHaveCount(2);
  const before = await page.getByTestId("invoice-row").evaluateAll((rows) => rows.map((row) => row.getAttribute("data-status")));
  await page.waitForTimeout(2500); // long enough for the slow answer to arrive, if it ever could
  expect(await page.getByTestId("invoice-row").evaluateAll((rows) => rows.map((row) => row.getAttribute("data-status")))).toEqual(before);
  await expect(page.getByTestId("invoice-notice")).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`/o/${b.orgId}/invoices$`)); // nothing navigated this screen away
  expect(testRow(`select status from invoices where id = ${sql(m.b.draft.id)}`)).toBe("draft"); // B's draft was never touched
});
