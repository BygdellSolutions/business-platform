import type { BrowserContext } from "@playwright/test";

import { expect, test } from "./fixtures";
import { bffUrl, createWorld, signIn, type World } from "./support";

/**
 * Scheduling: appointments are booked in the browser, shown per day (a column per person), week and month, changed, cancelled and
 * deleted; two bookings of one person that overlap are marked; another organization's calendar is invisible.
 */

let world: World;
let other: World;
test.afterEach(() => {
  world?.cleanup();
  other?.cleanup();
});

const WEEK = "view=week&date=2026-10-19"; // Monday 19 October 2026 (the organization has no time zone set, so times are UTC)

async function book(context: BrowserContext, orgId: string, body: Record<string, unknown>) {
  const response = await context.request.post(bffUrl(orgId, "/appointments"), { data: body });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; version: number };
}

async function people(context: BrowserContext, orgId: string) {
  const response = await context.request.get(bffUrl(orgId, "/members/people?journal=true"));
  return (await response.json()) as { user_id: string; name: string }[];
}

test("an appointment is booked, changed, cancelled and deleted in the browser", async ({ page, context }) => {
  world = createWorld({ label: "Calendar" });
  await signIn(context, world.email);

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await expect(page.getByTestId("calendar-title")).toHaveText("19–25 October 2026");
  await expect(page.getByTestId("appointment")).toHaveCount(0);

  await page.getByTestId("new-appointment").click();
  await page.getByLabel("Title", { exact: true }).fill("Massage Kalle");
  await page.getByLabel("Start time").fill("09:00");
  await page.getByLabel("End time").fill("10:30");
  await page.getByLabel("Practitioner").selectOption({ index: 1 });
  await page.getByTestId("submit").click();

  await expect(page.getByTestId("appointment")).toHaveCount(1);
  await expect(page.getByTestId("appointment")).toContainText("Massage Kalle");
  await expect(page.getByTestId("appointment")).toContainText("09:00–10:30");

  await page.getByTestId("appointment").click();
  await expect(page.getByTestId("record-name")).toHaveText("Massage Kalle");
  await page.getByLabel("Title", { exact: true }).fill("Massage Kalle (rescheduled)");
  await page.getByLabel("Start time").fill("11:00");
  await page.getByLabel("End time").fill("12:00");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Start time")).toHaveValue("11:00");

  await page.getByTestId("cancel-appointment").click();
  await page.getByRole("button", { name: "Yes, cancel it" }).click();
  await expect(page.getByTestId("cancelled")).toBeVisible();
  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await expect(page.getByTestId("appointment")).toHaveCount(0); // a cancelled appointment is not shown

  const [mine] = await (await context.request.get(bffUrl(world.orgId, "/appointments?from=2026-10-19T00:00:00Z&to=2026-10-26T00:00:00Z&include_cancelled=true"))).json();
  await page.goto(`/o/${world.orgId}/calendar/${mine.id}`);
  await page.getByTestId("restore").click();
  await expect(page.getByTestId("saved")).toBeVisible();
  await page.getByTestId("delete-appointment").click();
  await page.getByRole("button", { name: "Yes, delete it" }).click();
  await expect(page.getByTestId("calendar")).toBeVisible();
  await expect(page.getByTestId("appointment")).toHaveCount(0);
  expect((await context.request.get(bffUrl(world.orgId, `/appointments/${mine.id}`))).status()).toBe(404);
});

test("the day shows a column per person, the week and month show everyone, and a person can be picked", async ({ page, context }) => {
  world = createWorld({ label: "Calendar views" });
  world.addMember("employee");
  await signIn(context, world.email);
  const [colleague, owner] = await people(context, world.orgId); // by name: employee, owner
  await book(context, world.orgId, { title: "Owner's visit", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:00:00Z", practitioner_user_id: owner.user_id });
  await book(context, world.orgId, { title: "Colleague's visit", starts_at: "2026-10-19T08:30:00Z", ends_at: "2026-10-19T09:30:00Z", practitioner_user_id: colleague.user_id });
  await book(context, world.orgId, { title: "Nobody's visit", starts_at: "2026-10-20T10:00:00Z", ends_at: "2026-10-20T11:00:00Z" });

  await page.goto(`/o/${world.orgId}/calendar?view=day&date=2026-10-19`);
  await expect(page.getByTestId("column-heading")).toHaveText([colleague.name, owner.name]);
  await expect(page.getByTestId("column").first().getByTestId("appointment")).toHaveText(/Colleague's visit/);
  await expect(page.getByTestId("column").last().getByTestId("appointment")).toHaveText(/Owner's visit/);

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await expect(page.getByTestId("appointment")).toHaveCount(3);
  await page.getByTestId("person-filter").filter({ hasText: owner.name }).click();
  await expect(page.getByTestId("appointment")).toHaveCount(1);
  await expect(page.getByTestId("appointment")).toContainText("Owner's visit");

  await page.goto(`/o/${world.orgId}/calendar?view=month&date=2026-10-19`);
  await expect(page.getByTestId("appointment")).toHaveCount(3);
  await expect(page.getByTestId("month-day").filter({ hasText: "Nobody's visit" })).toHaveAttribute("data-day", "2026-10-20");
  await page.getByTestId("calendar-next").click();
  await expect(page.getByTestId("calendar-title")).toHaveText("November 2026");
  await expect(page.getByTestId("appointment")).toHaveCount(0);
});

test("two bookings of one person that overlap are marked, two people at once are not", async ({ page, context }) => {
  world = createWorld({ label: "Calendar clash" });
  world.addMember("employee");
  await signIn(context, world.email);
  const [colleague, owner] = await people(context, world.orgId);
  await book(context, world.orgId, { title: "First", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:00:00Z", practitioner_user_id: owner.user_id });
  await book(context, world.orgId, { title: "Second", starts_at: "2026-10-19T08:30:00Z", ends_at: "2026-10-19T09:30:00Z", practitioner_user_id: owner.user_id });
  await book(context, world.orgId, { title: "Elsewhere", starts_at: "2026-10-19T08:30:00Z", ends_at: "2026-10-19T09:30:00Z", practitioner_user_id: colleague.user_id });

  await page.goto(`/o/${world.orgId}/calendar?view=day&date=2026-10-19`);
  await expect(page.getByTestId("appointment").filter({ hasText: "First" })).toHaveAttribute("data-clash", "true");
  await expect(page.getByTestId("appointment").filter({ hasText: "Second" })).toHaveAttribute("data-clash", "true");
  await expect(page.getByTestId("appointment").filter({ hasText: "Elsewhere" })).not.toHaveAttribute("data-clash", "true");
});

test("another organization's appointments are invisible, and its detail page is the generic not-found page", async ({ page, context }) => {
  world = createWorld({ label: "Calendar A" });
  other = createWorld({ label: "Calendar B" });
  await signIn(context, other.email);
  const theirs = await book(context, other.orgId, { title: "Secret treatment", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:00:00Z" });
  await signIn(context, world.email);

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await expect(page.getByTestId("appointment")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("Secret treatment");

  async function outcome(id: string) {
    const response = await page.goto(`/o/${world.orgId}/calendar/${id}`);
    await expect(page.getByTestId("not-found")).toBeVisible();
    return { status: response?.status(), text: await page.locator("body").innerText() };
  }
  expect(await outcome(theirs.id)).toEqual(await outcome("00000000-0000-4000-8000-00000000dead"));
});

test("a viewer sees the calendar but cannot book", async ({ page, context }) => {
  world = createWorld({ label: "Calendar viewer" });
  await signIn(context, world.email);
  await book(context, world.orgId, { title: "Visible to all", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T09:00:00Z" });
  await signIn(context, world.addMember("viewer"));

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await expect(page.getByTestId("appointment")).toHaveCount(1);
  await expect(page.getByTestId("new-appointment")).toHaveCount(0);
  await expect(page.getByTestId("slot")).toHaveCount(0);
  await page.goto(`/o/${world.orgId}/calendar/new`);
  await expect(page.getByTestId("not-allowed")).toBeVisible();
});
