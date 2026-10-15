import { expect, test, type Page } from "./fixtures";

import { bffUrl, createCustomer, createHorse, createWorld, signIn, sql, testRow, type World } from "./support";

/**
 * The horse journal in the browser: an entry is started, drawn on, written, saved, reloaded, finalized and corrected; marks stay
 * aligned when the screen size changes and can be drawn with a finger; accountants and viewers see nothing of it.
 */

let world: World;
test.afterEach(() => world?.cleanup());

async function startEntry(page: Page, orgId: string, horseId: string) {
  await page.goto(`/o/${orgId}/horses/${horseId}`);
  await page.getByTestId("journal-toggle").getByRole("heading", { name: /Journal of visits and treatments/ }).click(); // a section like the services
  await page.getByTestId("new-journal-entry").click();
  await expect(page.getByTestId("journal-editor")).toBeVisible();
}

async function box(page: Page, side = "left") {
  const found = await page.getByTestId(`diagram-${side}`).boundingBox();
  if (!found) throw new Error("the diagram is not on the page");
  return found;
}

test("an entry is drawn, written, saved, reloaded, finalized and corrected", async ({ page, context }) => {
  world = createWorld({ label: "Journal" });
  await signIn(context, world.email);
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: (await createCustomer(context, world.orgId, "Anna Andersson")).id });
  await startEntry(page, world.orgId, horse.id);

  // an X with a click, an arrow with a drag
  await page.getByTestId("tool-x").click();
  const area = await box(page);
  await page.mouse.click(area.x + area.width * 0.45, area.y + area.height * 0.45);
  await page.getByTestId("tool-arrow").click();
  await page.mouse.move(area.x + area.width * 0.7, area.y + area.height * 0.2);
  await page.mouse.down();
  await page.mouse.move(area.x + area.width * 0.6, area.y + area.height * 0.3, { steps: 4 });
  await page.mouse.move(area.x + area.width * 0.55, area.y + area.height * 0.4, { steps: 4 });
  await page.mouse.up();
  await expect(page.getByTestId("marks-list").locator("li")).toHaveCount(2);
  // each mark carries a number on the picture and in the list, so the text can refer to "mark 2"
  await expect(page.getByTestId("diagram-left").getByTestId("mark-number")).toHaveText(["1", "2"]);
  await expect(page.getByTestId("marks-list").locator("li").first()).toContainText("1. X on the horse's left side");
  await expect(page.getByTestId("marks-list").locator("li").nth(1)).toContainText("2. Arrow on the horse's left side");

  await page.getByLabel("Follow-up date (optional)").fill("2026-11-12");
  await page.getByLabel(/Observations/).fill("Stiff left shoulder.\nTreated with massage; better afterwards.");
  await expect(page.getByTestId("save-status")).toHaveText("Unsaved changes");
  await page.getByTestId("save-draft").click();
  await expect(page.getByTestId("save-status")).toHaveText("All changes saved");

  await page.reload();
  await expect(page.getByTestId("marks-list").locator("li")).toHaveCount(2); // the marks came back from the database
  await expect(page.getByTestId("diagram-left").getByTestId("mark-number")).toHaveText(["1", "2"]); // with their numbers
  await expect(page.getByLabel(/Observations/)).toHaveValue(/Stiff left shoulder/);

  await page.getByTestId("finalize").click();
  await page.getByTestId("finalize-confirm").click();
  await expect(page.getByTestId("entry-final")).toContainText("Finalized");
  await expect(page.getByTestId("journal-editor")).toHaveCount(0);
  await expect(page.getByTestId("entry-body")).toContainText("Treated with massage");

  await page.getByLabel(/Add a correction/).fill("It was the right shoulder, not the left.");
  await page.getByTestId("add-correction").click();
  await expect(page.getByTestId("correction")).toHaveCount(1);
  await expect(page.getByTestId("entry-body")).toContainText("Stiff left shoulder"); // the entry itself is unchanged

  await page.getByRole("link", { name: "All entries" }).click();
  await expect(page.getByTestId("journal-row")).toHaveCount(1);
  await expect(page.getByTestId("entry-status")).toHaveText("Finalized");
  // a table like the other lists: No., Visit, By, Status, Marks, Notes
  const headings = page.getByTestId("journal-table").getByRole("columnheader");
  await expect(headings).toHaveCount(7);
  for (const [index, label] of ["No.", "Visit", "By", "Status", "Marks", "Follow-up", "Notes"].entries()) await expect(headings.nth(index)).toContainText(label);
  await expect(page.getByTestId("journal-row").getByTestId("record-number")).toHaveText("1");
  await expect(page.getByTestId("journal-row").getByTestId("entry-follow-up")).toHaveText("2026-11-12"); // the follow-up date is in the list
  await expect(page.getByTestId("journal-row")).toContainText("Stiff left shoulder");

  // and the entry is listed in the journal section of the horse's own page
  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  await expect(page.getByTestId("journal-section")).toContainText("(1)");
  await page.getByTestId("journal-toggle").getByRole("heading", { name: /Journal of visits and treatments/ }).click();
  await expect(page.getByTestId("journal-section").getByTestId("journal-row")).toHaveCount(1);
  await expect(page.getByTestId("journal-section").getByTestId("entry-status")).toHaveText("Finalized");
  await expect(page.getByTestId("journal-section").getByTestId("entry-follow-up")).toHaveText("2026-11-12"); // and in the quick view on the horse page
  // the two things to do here look alike: "New entry" and "Open the journal" are the same kind of button
  const [fresh, open] = [page.getByTestId("new-journal-entry"), page.getByTestId("journal-all")];
  await expect(open).toHaveText("Open the journal");
  const [a, b] = [await fresh.boundingBox(), await open.boundingBox()];
  expect(a && b && Math.abs(a.height - b.height) < 1).toBe(true);
  expect(await fresh.evaluate((e) => getComputedStyle(e).backgroundColor)).toBe(await open.evaluate((e) => getComputedStyle(e).backgroundColor));
});

test("a draft can be deleted but a finalized entry cannot", async ({ page, context }) => {
  world = createWorld({ label: "JournalDelete" });
  await signIn(context, world.email);
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: (await createCustomer(context, world.orgId, "Anna Andersson")).id });

  await startEntry(page, world.orgId, horse.id);
  await page.getByLabel(/Observations/).fill("A draft I will throw away");
  await page.getByTestId("save-draft").click();
  await expect(page.getByTestId("save-status")).toHaveText("All changes saved");
  await page.getByTestId("delete-draft").click();
  await expect(page.getByText(/Only a draft can be deleted/)).toBeVisible(); // it asks first
  await page.getByTestId("delete-draft-confirm").click();
  await expect(page.getByTestId("empty")).toHaveText("No journal entries yet.");
  expect(testRow(`select count(*) from horse_journal_entries where horse_id = ${sql(horse.id)}`)).toBe("0"); // really gone

  await startEntry(page, world.orgId, horse.id);
  await page.getByLabel(/Observations/).fill("This one is kept");
  await page.getByTestId("finalize").click();
  await page.getByTestId("finalize-confirm").click();
  await expect(page.getByTestId("entry-final")).toBeVisible();
  await expect(page.getByTestId("delete-draft")).toHaveCount(0); // no delete on a finalized entry
  const entry = testRow(`select id from horse_journal_entries where horse_id = ${sql(horse.id)}`);
  const forged = await context.request.delete(bffUrl(world.orgId, `/horses/${horse.id}/journal/${entry}`), { headers: { "if-match": '"2"' } });
  expect(forged.status()).toBe(409);
  expect(testRow(`select count(*) from horse_journal_entries where horse_id = ${sql(horse.id)}`)).toBe("1");
});

test("the practitioner is chosen from the organization's members, like who performed a service on an order", async ({ page, context }) => {
  world = createWorld({ label: "JournalWho" });
  await signIn(context, world.email);
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: (await createCustomer(context, world.orgId, "Anna Andersson")).id });
  const colleague = world.addMember("employee");
  await startEntry(page, world.orgId, horse.id);
  const choice = page.getByLabel("Practitioner");
  await expect(choice.locator("option")).toHaveCount(2); // the two members
  await choice.selectOption({ index: 1 });
  const chosen = await choice.locator("option:checked").textContent();
  await page.getByLabel(/Observations/).fill("Done by a colleague.");
  await page.getByTestId("save-draft").click();
  await expect(page.getByTestId("save-status")).toHaveText("All changes saved");
  await page.reload();
  await expect(choice.locator("option:checked")).toHaveText(chosen ?? "");
  expect(colleague).toBeTruthy();
});

test("a mark stays in the same place on the horse when the screen size changes", async ({ page, context }) => {
  world = createWorld({ label: "JournalResize" });
  await signIn(context, world.email);
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: (await createCustomer(context, world.orgId, "Anna Andersson")).id });
  await page.setViewportSize({ width: 1200, height: 900 });
  await startEntry(page, world.orgId, horse.id);
  await page.getByTestId("tool-o").click();
  const wide = await box(page);
  await page.mouse.click(wide.x + wide.width * 0.3, wide.y + wide.height * 0.7);

  const fraction = async () => {
    const area = await box(page);
    const mark = await page.getByTestId("mark").boundingBox();
    if (!mark) throw new Error("no mark");
    return { x: (mark.x + mark.width / 2 - area.x) / area.width, y: (mark.y + mark.height / 2 - area.y) / area.height, width: area.width };
  };
  const before = await fraction();
  await page.setViewportSize({ width: 390, height: 800 });
  const after = await fraction();
  expect(after.width).toBeLessThan(before.width); // the diagram really shrank
  expect(after.x).toBeCloseTo(before.x, 1);
  expect(after.y).toBeCloseTo(before.y, 1);
  expect(after.x).toBeCloseTo(0.3, 1);
  expect(after.y).toBeCloseTo(0.7, 1);
});

test.describe("on a touch screen", () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

  test("a finger draws a mark and the page does not scroll instead", async ({ page, context }) => {
    world = createWorld({ label: "JournalTouch" });
    await signIn(context, world.email);
    const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: (await createCustomer(context, world.orgId, "Anna Andersson")).id });
    await startEntry(page, world.orgId, horse.id);
    await page.getByTestId("tool-x").tap();
    await page.getByTestId("diagram-left").scrollIntoViewIfNeeded(); // on a phone the diagram starts below the first screen
    const area = await box(page);
    await page.touchscreen.tap(area.x + area.width * 0.5, area.y + area.height * 0.5);
    await expect(page.getByTestId("marks-list").locator("li")).toHaveCount(1);
    expect(await page.getByTestId("diagram-left").evaluate((element) => getComputedStyle(element).touchAction)).toBe("none");
  });
});

test("an accountant and a viewer have no journal at all, in the browser or by a forged request", async ({ page, context }) => {
  world = createWorld({ label: "JournalRoles" });
  await signIn(context, world.email);
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: (await createCustomer(context, world.orgId, "Anna Andersson")).id });

  for (const role of ["accountant", "viewer"] as const) {
    await signIn(context, world.addMember(role));
    await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
    await expect(page.getByTestId("record-name")).toBeVisible();
    await expect(page.getByTestId("journal-section")).toHaveCount(0); // no section, and nothing asked of the backend for it
    await page.goto(`/o/${world.orgId}/horses/${horse.id}/journal`);
    await expect(page.getByTestId("journal-not-allowed")).toBeVisible();
    const forged = await context.request.get(`/api/o/${world.orgId}/horses/${horse.id}/journal`);
    expect(forged.status()).toBe(403);
  }
});
