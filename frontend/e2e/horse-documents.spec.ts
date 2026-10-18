import { expect, test } from "./fixtures";

import { bffUrl, createCustomer, createHorse, createWorld, signIn, type World } from "./support";

/**
 * Documents on a horse: a PDF or a picture is added on the horse's page, shown (a picture as a preview), opened, and deleted; the
 * history keeps the file's name; a viewer can only open them; another organization can neither list nor open them.
 */

let world: World;
let other: World;
test.afterEach(() => {
  world?.cleanup();
  other?.cleanup();
});

// A real 1×1 PNG, so the browser can draw the preview.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const PDF = Buffer.from("%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n");

async function horseOf(context: Parameters<typeof createCustomer>[0], orgId: string) {
  const anna = await createCustomer(context, orgId, "Anna Andersson");
  return createHorse(context, orgId, { name: "Kalle", owner_customer_id: anna.id });
}

test("a PDF and a picture are added, shown, opened and deleted, and the history keeps their names", async ({ page, context }) => {
  world = createWorld({ label: "HorseDocs" });
  await signIn(context, world.email);
  const horse = await horseOf(context, world.orgId);

  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  const docs = page.getByTestId("horse-documents");
  await expect(docs.getByTestId("no-documents")).toBeVisible();
  await expect(docs.getByTestId("add-document")).toBeDisabled(); // nothing chosen yet

  await docs.getByTestId("document-input").setInputFiles({ name: "Vet report.pdf", mimeType: "application/pdf", buffer: PDF });
  await expect(docs.getByTestId("document-chosen")).toContainText("Vet report.pdf");
  await docs.getByTestId("add-document").click();
  await expect(docs.getByTestId("horse-document")).toHaveCount(1);

  await docs.getByTestId("document-input").setInputFiles({ name: "Hoof x-ray.png", mimeType: "image/png", buffer: PNG });
  await docs.getByTestId("add-document").click();
  await expect(docs.getByTestId("horse-document")).toHaveCount(2);
  await expect(docs.getByTestId("document-chosen")).toHaveText("PDF or picture, up to 10 MB"); // cleared after adding

  // the picture is drawn as a preview (it really loaded), the PDF is a name with a PDF mark
  const picture = docs.getByTestId("horse-document").filter({ hasText: "Hoof x-ray.png" });
  await expect(picture.getByTestId("document-preview")).toBeVisible();
  await expect.poll(() => picture.getByTestId("document-preview").evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await expect(docs.getByTestId("horse-document").filter({ hasText: "Vet report.pdf" }).getByTestId("document-preview")).toHaveCount(0);

  // opening: the very bytes, with the safe headers
  const href = await picture.getByTestId("document-name").getAttribute("href");
  const opened = await context.request.get(href!);
  expect(opened.status()).toBe(200);
  expect(Buffer.from(await opened.body()).equals(PNG)).toBe(true);
  expect(opened.headers()["content-type"]).toBe("image/png");
  expect(opened.headers()["x-content-type-options"]).toBe("nosniff");
  expect(opened.headers()["content-disposition"]).toContain('filename="Hoof x-ray.png"');
  await expect(picture.getByTestId("document-name")).toHaveAttribute("target", "_blank");

  // it is still there after a reload
  await page.reload();
  await expect(docs.getByTestId("horse-document")).toHaveCount(2);

  await docs.getByTestId("horse-document").filter({ hasText: "Vet report.pdf" }).getByTestId("delete-document").click();
  await page.getByRole("button", { name: "Yes, delete" }).click();
  await expect(docs.getByTestId("horse-document")).toHaveCount(1);

  await page.getByTestId("history-toggle").getByRole("heading", { name: /History/ }).click();
  const events = page.getByTestId("history-event").filter({ hasText: "Document" });
  await expect(events).toHaveCount(3); // two added, one deleted
  await expect(events.filter({ hasText: "Vet report.pdf" })).not.toHaveCount(0);
});

test("a file that is not a PDF or a picture, or is too big, is stopped in the form and by the server", async ({ page, context }) => {
  world = createWorld({ label: "HorseDocsBad" });
  await signIn(context, world.email);
  const horse = await horseOf(context, world.orgId);

  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  const docs = page.getByTestId("horse-documents");
  await docs.getByTestId("document-input").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(docs.getByTestId("document-problem")).toHaveText("Choose a PDF, JPEG, PNG or WebP file.");
  await expect(docs.getByTestId("add-document")).toBeDisabled();

  await docs.getByTestId("document-input").setInputFiles({ name: "huge.pdf", mimeType: "application/pdf", buffer: Buffer.alloc(10 * 1024 * 1024 + 1, 0x41) });
  await expect(docs.getByTestId("document-problem")).toHaveText("The file is larger than 10 MB.");

  // a forged request: a program that claims to be a PDF, and a page that claims to be a picture
  const url = bffUrl(world.orgId, `/horses/${horse.id}/documents`);
  const fake = await context.request.post(url, { data: { filename: "evil.pdf", content_type: "application/pdf", data_base64: Buffer.from("MZ\x90\x00 not a pdf").toString("base64") } });
  expect(fake.status()).toBe(422);
  const html = await context.request.post(url, { data: { filename: "x.png", content_type: "text/html", data_base64: Buffer.from("<script>1</script>").toString("base64") } });
  expect(html.status()).toBe(422);
  expect(await (await context.request.get(url)).json()).toEqual([]);
});

test("a file of just under 10 MB goes through the whole chain", async ({ page, context }) => {
  world = createWorld({ label: "HorseDocsBig" });
  await signIn(context, world.email);
  const horse = await horseOf(context, world.orgId);
  const big = Buffer.concat([Buffer.from("%PDF-1.4\n"), Buffer.alloc(9 * 1024 * 1024, 0x41)]);

  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  const docs = page.getByTestId("horse-documents");
  await docs.getByTestId("document-input").setInputFiles({ name: "Scan.pdf", mimeType: "application/pdf", buffer: big });
  await docs.getByTestId("add-document").click();
  await expect(docs.getByTestId("horse-document")).toHaveCount(1, { timeout: 60_000 });
  await expect(docs.getByTestId("horse-document")).toContainText("9.0 MB");

  const href = await docs.getByTestId("document-name").getAttribute("href");
  const opened = await context.request.get(href!);
  expect(opened.status()).toBe(200);
  expect(Buffer.from(await opened.body()).equals(big)).toBe(true);
});

test("a viewer can open a horse's documents but cannot add or delete them", async ({ page, context }) => {
  world = createWorld({ label: "HorseDocsViewer" });
  await signIn(context, world.email);
  const horse = await horseOf(context, world.orgId);
  const made = await context.request.post(bffUrl(world.orgId, `/horses/${horse.id}/documents`), {
    data: { filename: "Passport.pdf", content_type: "application/pdf", data_base64: PDF.toString("base64") },
  });
  expect(made.status()).toBe(201);
  const document = (await made.json()) as { id: string };

  await signIn(context, world.addMember("viewer"));
  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  const docs = page.getByTestId("horse-documents");
  await expect(docs.getByTestId("horse-document")).toHaveCount(1);
  await expect(docs.getByTestId("document-input")).toHaveCount(0);
  await expect(docs.getByTestId("delete-document")).toHaveCount(0);
  expect((await context.request.get(bffUrl(world.orgId, `/horses/${horse.id}/documents/${document.id}/file`))).status()).toBe(200);
  expect((await context.request.delete(bffUrl(world.orgId, `/horses/${horse.id}/documents/${document.id}`))).status()).toBe(403);
});

test("another organization can neither list nor open a horse's documents", async ({ context }) => {
  world = createWorld({ label: "HorseDocsA" });
  other = createWorld({ label: "HorseDocsB" });
  await signIn(context, world.email);
  const horse = await horseOf(context, world.orgId);
  const made = await context.request.post(bffUrl(world.orgId, `/horses/${horse.id}/documents`), {
    data: { filename: "Secret.pdf", content_type: "application/pdf", data_base64: PDF.toString("base64") },
  });
  const document = (await made.json()) as { id: string };

  await signIn(context, other.email);
  // asked through the other organization, and through the first one without being a member of it
  expect((await context.request.get(bffUrl(other.orgId, `/horses/${horse.id}/documents`))).status()).toBe(404);
  expect((await context.request.get(bffUrl(other.orgId, `/horses/${horse.id}/documents/${document.id}/file`))).status()).toBe(404);
  expect((await context.request.get(bffUrl(world.orgId, `/horses/${horse.id}/documents/${document.id}/file`))).status()).toBe(404);
  expect((await context.request.delete(bffUrl(other.orgId, `/horses/${horse.id}/documents/${document.id}`))).status()).toBe(404);
});
