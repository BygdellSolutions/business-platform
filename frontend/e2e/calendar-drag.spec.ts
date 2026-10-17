import type { BrowserContext, Locator, Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { bffUrl, createWorld, signIn, type World } from "./support";

/**
 * Drag and drop in the calendar: a booking is moved to another time, day or person, its end is dragged, and what must not change
 * does not (a click, a drop outside the grid, a booking someone else changed meanwhile, a viewer).
 */

let world: World;
test.afterEach(() => world?.cleanup());

const WEEK = "view=week&date=2026-10-19"; // Monday 19 October 2026 (the organization has no time zone set, so times are UTC)

async function book(context: BrowserContext, orgId: string, body: Record<string, unknown>) {
  const response = await context.request.post(bffUrl(orgId, "/appointments"), { data: body });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; version: number };
}

async function people(context: BrowserContext, orgId: string) {
  return (await (await context.request.get(bffUrl(orgId, "/members/people?journal=true"))).json()) as { user_id: string; name: string }[];
}

async function appointmentOf(context: BrowserContext, orgId: string, id: string) {
  return (await (await context.request.get(bffUrl(orgId, `/appointments/${id}`))).json()) as {
    starts_at: string;
    ends_at: string;
    practitioner_user_id: string | null;
    version: number;
    title: string;
  };
}

const instant = (value: string) => new Date(value).toISOString();

/** Press in the middle of a booking (`at` = 1 presses its bottom edge), move in steps by (dx, dy) and release. */
async function dragBy(page: Page, target: Locator, dx: number, dy: number, at = 0.5): Promise<void> {
  await expect(target).toBeVisible(); // the page is drawn before it is measured
  await page.waitForLoadState("networkidle"); // and its scripts are attached
  const box = (await target.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = at === 1 ? box.y + box.height - 2 : box.y + box.height * at;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx / 2, y + dy / 2, { steps: 6 });
  await page.mouse.move(x + dx, y + dy, { steps: 6 });
  await page.mouse.up();
}

test("a booking is dragged to another time and day in the week view, keeping its length", async ({ page, context }) => {
  world = createWorld({ label: "Drag week" });
  await signIn(context, world.email);
  const booked = await book(context, world.orgId, { title: "Massage", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:30:00Z" });

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await expect(page.getByTestId("appointment")).toBeVisible();
  const columns = page.getByTestId("column");
  const monday = (await columns.nth(0).boundingBox())!;
  const tuesday = (await columns.nth(1).boundingBox())!;
  await dragBy(page, page.getByTestId("appointment"), tuesday.x - monday.x, 96); // one column right, two hours (48 px each) down
  await expect(page.getByTestId("calendar-message")).toHaveText("Saved.");

  const moved = await appointmentOf(context, world.orgId, booked.id);
  expect([instant(moved.starts_at), instant(moved.ends_at)]).toEqual(["2026-10-20T10:00:00.000Z", "2026-10-20T11:30:00.000Z"]);
  await expect(page.getByTestId("column").nth(1).getByTestId("appointment")).toHaveCount(1); // the page shows it in its new place
  await expect(page.getByTestId("column").nth(0).getByTestId("appointment")).toHaveCount(0);
  await expect(page.getByTestId("appointment")).toContainText("10:00–11:30");
});

test("a booking dragged to another person's column in the day view changes its practitioner", async ({ page, context }) => {
  world = createWorld({ label: "Drag person" });
  world.addMember("employee");
  await signIn(context, world.email);
  const [colleague, owner] = await people(context, world.orgId); // by name: employee, owner
  const booked = await book(context, world.orgId, { title: "Visit", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:00:00Z", practitioner_user_id: owner.user_id });

  await page.goto(`/o/${world.orgId}/calendar?view=day&date=2026-10-19`);
  await expect(page.getByTestId("appointment")).toBeVisible();
  const first = (await page.getByTestId("column").nth(0).boundingBox())!;
  const second = (await page.getByTestId("column").nth(1).boundingBox())!;
  await dragBy(page, page.getByTestId("appointment"), first.x - second.x, 0); // left, into the colleague's column, same time
  await expect(page.getByTestId("calendar-message")).toHaveText("Saved.");

  const moved = await appointmentOf(context, world.orgId, booked.id);
  expect(moved.practitioner_user_id).toBe(colleague.user_id);
  expect(instant(moved.starts_at)).toBe("2026-10-19T08:00:00.000Z"); // the time did not change
  await expect(page.getByTestId("column").nth(0).getByTestId("appointment")).toHaveCount(1);
});

test("the bottom edge of a booking is dragged to change when it ends", async ({ page, context }) => {
  world = createWorld({ label: "Drag resize" });
  await signIn(context, world.email);
  const booked = await book(context, world.orgId, { title: "Session", starts_at: "2026-10-19T09:00:00Z", ends_at: "2026-10-19T10:00:00Z" });

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await dragBy(page, page.getByTestId("appointment"), 0, 72, 1); // the bottom edge, an hour and a half down
  await expect(page.getByTestId("calendar-message")).toHaveText("Saved.");
  const longer = await appointmentOf(context, world.orgId, booked.id);
  expect([instant(longer.starts_at), instant(longer.ends_at)]).toEqual(["2026-10-19T09:00:00.000Z", "2026-10-19T11:30:00.000Z"]);

  await dragBy(page, page.getByTestId("appointment"), 0, -300, 1); // far above the start: not after it, so nothing changes
  await expect(page.getByTestId("appointment")).toBeVisible();
  const same = await appointmentOf(context, world.orgId, booked.id);
  expect(instant(same.ends_at)).toBe("2026-10-19T11:30:00.000Z");
  expect(same.version).toBe(longer.version);
});

test("a booking is dropped on another day in the month view, keeping its time", async ({ page, context }) => {
  world = createWorld({ label: "Drag month" });
  await signIn(context, world.email);
  const booked = await book(context, world.orgId, { title: "Clinic", starts_at: "2026-10-19T08:30:00Z", ends_at: "2026-10-19T10:00:00Z" });

  await page.goto(`/o/${world.orgId}/calendar?view=month&date=2026-10-19`);
  await expect(page.getByTestId("appointment")).toBeVisible();
  const target = (await page.locator('[data-month-day][data-day="2026-10-22"]').boundingBox())!;
  const chip = (await page.getByTestId("appointment").boundingBox())!;
  await dragBy(page, page.getByTestId("appointment"), target.x + target.width / 2 - (chip.x + chip.width / 2), target.y + 60 - (chip.y + chip.height / 2));
  await expect(page.getByTestId("calendar-message")).toHaveText("Saved.");
  const moved = await appointmentOf(context, world.orgId, booked.id);
  expect([instant(moved.starts_at), instant(moved.ends_at)]).toEqual(["2026-10-22T08:30:00.000Z", "2026-10-22T10:00:00.000Z"]);
  await expect(page.locator('[data-month-day][data-day="2026-10-22"]').getByTestId("appointment")).toHaveCount(1);
});

test("a small movement is still a click that opens the booking, and dropping outside the grid changes nothing", async ({ page, context }) => {
  world = createWorld({ label: "Drag click" });
  await signIn(context, world.email);
  const booked = await book(context, world.orgId, { title: "Quick look", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:00:00Z" });

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await dragBy(page, page.getByTestId("appointment"), 2, 2);
  await expect(page.getByTestId("record-name")).toHaveText("Quick look");

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await dragBy(page, page.getByTestId("appointment"), 0, -400); // up above the grid
  await expect(page.getByTestId("calendar")).toBeVisible();
  const same = await appointmentOf(context, world.orgId, booked.id);
  expect(instant(same.starts_at)).toBe("2026-10-19T08:00:00.000Z");
  expect(same.version).toBe(1);
});

test("a booking someone else changed meanwhile snaps back with a message and nothing is overwritten", async ({ page, context }) => {
  world = createWorld({ label: "Drag stale" });
  await signIn(context, world.email);
  const booked = await book(context, world.orgId, { title: "Original", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:00:00Z" });

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  const changed = await context.request.patch(bffUrl(world.orgId, `/appointments/${booked.id}`), { data: { title: "Changed elsewhere" }, headers: { "if-match": `"${booked.version}"` } });
  expect(changed.status()).toBe(200); // another person saved while this page was open
  await dragBy(page, page.getByTestId("appointment"), 0, 96);
  await expect(page.getByTestId("calendar-message")).toContainText("changed by someone else");

  const after = await appointmentOf(context, world.orgId, booked.id);
  expect([after.title, instant(after.starts_at)]).toEqual(["Changed elsewhere", "2026-10-19T08:00:00.000Z"]); // not moved, their change kept
  await expect(page.getByTestId("appointment")).toContainText("Changed elsewhere"); // the page was reloaded
});

test("a viewer cannot drag a booking", async ({ page, context }) => {
  world = createWorld({ label: "Drag viewer" });
  await signIn(context, world.email);
  const booked = await book(context, world.orgId, { title: "Fixed", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:00:00Z" });
  await signIn(context, world.addMember("viewer"));

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await expect(page.getByTestId("appointment")).toBeVisible();
  await expect(page.locator("[data-movable]")).toHaveCount(0);
  await expect(page.locator("[data-resize]")).toHaveCount(0);
  await dragBy(page, page.getByTestId("appointment"), 0, 96);
  const same = await appointmentOf(context, world.orgId, booked.id);
  expect(instant(same.starts_at)).toBe("2026-10-19T08:00:00.000Z");
});
