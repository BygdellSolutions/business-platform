import type { BrowserContext } from "@playwright/test";

import { expect, test } from "./fixtures";
import { bffUrl, createCustomer, createHorse, createItem, createWorld, ifMatch, picker, signIn, type World } from "./support";

/**
 * Services on a booking, and the draft order made from it: the button, the link to the order, bringing the order up to date after a horse
 * or a service is added, and the cases where it must say no.
 */

let world: World;
test.afterEach(() => world?.cleanup());

const WEEK = "view=week&date=2026-10-19";

async function setup(context: BrowserContext) {
  await signIn(context, world.email);
  const stable = await createCustomer(context, world.orgId, "Umeå HK");
  const horses = [];
  for (const name of ["Kalle", "Storm", "Bella"]) horses.push(await createHorse(context, world.orgId, { name, owner_customer_id: stable.id }));
  const massage = await createItem(context, world.orgId, { name: "Massage", unit: "session", price_ex_vat: "850.00" });
  const stretch = await createItem(context, world.orgId, { name: "Stretching", unit: "session", price_ex_vat: "400.00" });
  return { stable, horses, massage, stretch };
}

async function book(context: BrowserContext, body: Record<string, unknown>) {
  const response = await context.request.post(bffUrl(world.orgId, "/appointments"), {
    data: { title: "Stable day", starts_at: "2026-10-19T08:00:00Z", ends_at: "2026-10-19T10:00:00Z", ...body },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; version: number; order_id: string | null };
}

const horseRef = (horse: { id: string }) => ({ subject_type: "horse", subject_id: horse.id });

async function orderLines(context: BrowserContext, orderId: string) {
  const order = await (await context.request.get(bffUrl(world.orgId, `/transactions/${orderId}`))).json();
  return order.lines as { description: string; subject_id: string; version: number; id: string }[];
}

async function addFromPicker(page: import("@playwright/test").Page, field: string, name: string) {
  const input = picker(page, field).getByRole("combobox");
  await input.click();
  await input.fill(name);
  await picker(page, field).getByRole("option").filter({ hasText: name }).first().click();
}

test("services are chosen on a booking, and a draft order with a line for each service and horse is made from it", async ({ page, context }) => {
  world = createWorld({ label: "Booking order" });
  const { stable, horses } = await setup(context);

  await page.goto(`/o/${world.orgId}/calendar/new?date=2026-10-19&start=09:00`);
  await page.getByLabel("Title", { exact: true }).fill("Stable day");
  await addFromPicker(page, "subjects", "Kalle");
  await addFromPicker(page, "subjects", "Storm");
  await addFromPicker(page, "items", "Massage");
  await addFromPicker(page, "items", "Stretching");
  await expect(page.getByTestId("chosen-service")).toHaveText(["Massage×", "Stretching×"]);
  await addFromPicker(page, "customer_id", "Umeå");
  await page.getByTestId("submit").click();

  await page.getByTestId("appointment").click();
  await expect(page.getByTestId("chosen-service")).toHaveCount(2); // the services were saved
  await expect(page.getByTestId("create-order")).toBeEnabled();
  await page.getByTestId("create-order").click();
  await expect(page.getByTestId("order-notice")).toContainText("Draft order");
  await expect(page.getByTestId("appointment-order-link")).toContainText("Order 1001");
  await expect(page.getByTestId("appointment-order-status")).toHaveText("draft");
  await expect(page.getByTestId("create-order")).toHaveCount(0); // one order per appointment

  const [appointment] = await (await context.request.get(bffUrl(world.orgId, "/appointments?from=2026-10-19T00:00:00Z&to=2026-10-26T00:00:00Z"))).json();
  const lines = await orderLines(context, appointment.order_id);
  expect(lines).toHaveLength(4); // 2 services x 2 horses
  expect(new Set(lines.map((line) => line.subject_id))).toEqual(new Set([horses[0].id, horses[1].id]));

  await page.getByTestId("appointment-order-link").click();
  await expect(page.getByTestId("record-name")).toContainText("Order 1001");
  expect(stable.id).toBeTruthy();
});

test("a horse added after the order was made becomes lines with Update order, and what was typed on the order stays", async ({ page, context }) => {
  world = createWorld({ label: "Booking order update" });
  const { stable, horses, massage } = await setup(context);
  const booked = await book(context, { customer_id: stable.id, subjects: [horseRef(horses[0])], items: [massage.id] });
  const made = await (await context.request.post(bffUrl(world.orgId, `/appointments/${booked.id}/order`), { headers: ifMatch(booked.version) })).json();
  // someone adds a line of their own to the order
  const own = await context.request.post(bffUrl(world.orgId, `/transactions/${made.order_id}/lines`), { data: { description: "Travel", unit: "km", quantity: "30", unit_price_ex_vat: "5.00", vat_rate: "25" } });
  expect(own.status()).toBe(201);

  await page.goto(`/o/${world.orgId}/calendar/${booked.id}`);
  await expect(page.getByTestId("update-order")).toBeEnabled();
  await addFromPicker(page, "subjects", "Storm");
  await expect(page.getByTestId("update-order")).toBeDisabled(); // unsaved: the order is made from what is saved
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("update-order")).toBeEnabled();
  await page.getByTestId("update-order").click();
  await expect(page.getByTestId("order-notice")).toContainText("1 line added, 0 removed");

  const lines = await orderLines(context, made.order_id);
  expect(lines.map((line) => line.description).sort()).toEqual(["Massage", "Massage", "Travel"]);
});

test("the button says why it cannot make an order, and a completed or deleted order is handled", async ({ page, context }) => {
  world = createWorld({ label: "Booking order cases" });
  const { stable, horses, massage } = await setup(context);

  const bare = await book(context, { title: "Nothing chosen" });
  await page.goto(`/o/${world.orgId}/calendar/${bare.id}`);
  await expect(page.getByTestId("create-order")).toBeDisabled();
  await expect(page.getByTestId("appointment-order")).toContainText("Choose a customer");

  const noService = await book(context, { title: "No service", customer_id: stable.id });
  await page.goto(`/o/${world.orgId}/calendar/${noService.id}`);
  await expect(page.getByTestId("create-order")).toBeDisabled();
  await expect(page.getByTestId("appointment-order")).toContainText("Choose at least one service");

  const full = await book(context, { title: "Complete", customer_id: stable.id, subjects: [horseRef(horses[0])], items: [massage.id] });
  const made = await (await context.request.post(bffUrl(world.orgId, `/appointments/${full.id}/order`), { headers: ifMatch(full.version) })).json();
  const order = await (await context.request.get(bffUrl(world.orgId, `/transactions/${made.order_id}`))).json();
  expect((await context.request.post(bffUrl(world.orgId, `/transactions/${made.order_id}/complete`), { headers: ifMatch(order.version) })).status()).toBe(200);
  await page.goto(`/o/${world.orgId}/calendar/${full.id}`);
  await expect(page.getByTestId("appointment-order-status")).toHaveText("completed");
  await expect(page.getByTestId("update-order")).toBeDisabled();
  await expect(page.getByTestId("appointment-order")).toContainText("no longer a draft");

  const doomed = await book(context, { title: "Deleted order", customer_id: stable.id, items: [massage.id] });
  const second = await (await context.request.post(bffUrl(world.orgId, `/appointments/${doomed.id}/order`), { headers: ifMatch(doomed.version) })).json();
  const draft = await (await context.request.get(bffUrl(world.orgId, `/transactions/${second.order_id}`))).json();
  expect((await context.request.delete(bffUrl(world.orgId, `/transactions/${second.order_id}`), { headers: ifMatch(draft.version) })).status()).toBe(204);
  await page.goto(`/o/${world.orgId}/calendar/${doomed.id}`);
  await expect(page.getByTestId("order-gone")).toBeVisible();
  await page.getByTestId("create-order").click();
  await expect(page.getByTestId("appointment-order-link")).toBeVisible(); // made again
});

test("a viewer sees the services and the order but has no buttons", async ({ page, context }) => {
  world = createWorld({ label: "Booking order viewer" });
  const { stable, massage } = await setup(context);
  const booked = await book(context, { customer_id: stable.id, items: [massage.id] });
  await context.request.post(bffUrl(world.orgId, `/appointments/${booked.id}/order`), { headers: ifMatch(booked.version) });
  await signIn(context, world.addMember("viewer"));

  await page.goto(`/o/${world.orgId}/calendar/${booked.id}`);
  await expect(page.getByTestId("appointment-summary")).toContainText("Massage");
  await expect(page.getByTestId("appointment-summary")).toContainText("Order 1001 (draft)");
  await expect(page.getByTestId("create-order")).toHaveCount(0);
  await expect(page.getByTestId("update-order")).toHaveCount(0);
});

test("the calendar marks a booking that has an order", async ({ page, context }) => {
  world = createWorld({ label: "Booking order mark" });
  const { stable, horses, massage } = await setup(context);
  const booked = await book(context, { customer_id: stable.id, subjects: [horseRef(horses[0])], items: [massage.id], ends_at: "2026-10-19T12:00:00Z" });
  await context.request.post(bffUrl(world.orgId, `/appointments/${booked.id}/order`), { headers: ifMatch(booked.version) });

  await page.goto(`/o/${world.orgId}/calendar?${WEEK}`);
  await expect(page.getByTestId("appointment-order-mark")).toHaveText("Order 1001");
});
