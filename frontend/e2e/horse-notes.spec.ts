import { expect, test } from "./fixtures";

import { createCustomer, createHorse, createWorld, signIn, type World } from "./support";

/** Notes on a horse: added, edited and deleted on the horse's page, newest first, each step in the history. */

let world: World;
test.afterEach(() => world?.cleanup());

test("notes are taken on a horse, edited and deleted, and the history keeps every step", async ({ page, context }) => {
  world = createWorld({ label: "HorseNotes" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: anna.id });

  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  const notes = page.getByTestId("horse-notes");
  await expect(notes.getByTestId("no-notes")).toBeVisible();

  await notes.getByLabel("New note").fill("Stiff left shoulder.\nCheck again in two weeks.");
  await notes.getByTestId("add-note").click();
  await expect(notes.getByTestId("horse-note")).toHaveCount(1);
  await notes.getByLabel("New note").fill("Better after massage.");
  await notes.getByTestId("add-note").click();
  await expect(notes.getByTestId("horse-note")).toHaveCount(2);
  await expect(notes.getByTestId("horse-note-body").first()).toHaveText("Better after massage."); // newest first

  await notes.getByTestId("edit-note").first().click();
  await notes.getByLabel("Note", { exact: true }).fill("Much better after massage.");
  await notes.getByTestId("save-note").click();
  await expect(notes.getByTestId("horse-note-body").first()).toHaveText("Much better after massage.");

  await notes.getByTestId("delete-note").last().click();
  await page.getByRole("button", { name: "Yes, delete" }).click();
  await expect(notes.getByTestId("horse-note")).toHaveCount(1);

  await page.getByTestId("history-toggle").getByText(/Show history/).click();
  await expect(page.getByTestId("history-event").filter({ hasText: "Note" })).toHaveCount(4); // added twice, changed, deleted
});
