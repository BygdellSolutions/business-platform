import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { createWorld, signIn, sql, testRow, type World } from "./support";

/**
 * The logo in Settings, in a real browser: a picture is chosen, uploaded, shown and removed; one that is too big is made small enough
 * on the way (the person is not sent away to shrink it themselves); something that is not a picture is explained.
 */

let world: World;
test.afterEach(() => world?.cleanup());

// A real 1×1 PNG.
const TINY_PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

/** A picture the browser draws itself: `width` × `height` of random colour (it does not compress), as PNG or JPEG bytes. */
async function noisyPicture(page: Page, width: number, height: number, type: "image/png" | "image/jpeg"): Promise<Buffer> {
  const base64 = await page.evaluate(
    async ({ width, height, type }) => {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d")!;
      const pixels = context.createImageData(width, height);
      for (let i = 0; i < pixels.data.length; i += 4) {
        pixels.data[i] = Math.random() * 256;
        pixels.data[i + 1] = Math.random() * 256;
        pixels.data[i + 2] = Math.random() * 256;
        pixels.data[i + 3] = 255;
      }
      context.putImageData(pixels, 0, 0);
      return canvas.toDataURL(type, 0.95).split(",")[1];
    },
    { width, height, type },
  );
  return Buffer.from(base64, "base64");
}

test("a logo is chosen, uploaded and shown, replaced and removed", async ({ page, context }) => {
  world = createWorld({ label: "Logo" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}/settings`);
  const logo = page.getByRole("region", { name: "Logo" });
  await expect(logo.getByTestId("no-logo")).toBeVisible();
  await expect(logo.getByTestId("logo-upload")).toBeDisabled(); // nothing chosen yet
  await expect(logo.getByText("Upload logo", { exact: true })).toHaveCount(1); // said once: the button, not also a label above it

  await logo.getByTestId("logo-file").setInputFiles({ name: "logo.png", mimeType: "image/png", buffer: TINY_PNG });
  await expect(logo.getByTestId("logo-file-name")).toHaveText("logo.png");
  await logo.getByTestId("logo-upload").click();

  await expect(logo.getByTestId("logo-saved")).toBeVisible();
  await expect(logo.getByTestId("logo-preview")).toBeVisible();
  expect(testRow(`select count(*) from organization_logos where organization_id = ${sql(world.orgId)}`)).toBe("1");

  await page.reload();
  await expect(page.getByTestId("logo-preview")).toBeVisible(); // it is kept
  await page.getByTestId("logo-file").setInputFiles({ name: "again.png", mimeType: "image/png", buffer: TINY_PNG });
  await page.getByTestId("logo-upload").click();
  await expect(page.getByTestId("logo-saved")).toBeVisible();
  expect(testRow(`select count(*) from organization_logos where organization_id = ${sql(world.orgId)}`)).toBe("2"); // every version is kept

  await page.getByTestId("logo-remove").click();
  await expect(page.getByTestId("logo-removed")).toBeVisible();
  await expect(page.getByTestId("no-logo")).toBeVisible();
});

test("a picture that is too big is made small enough on the way", async ({ page, context }) => {
  world = createWorld({ label: "LogoBig" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}/settings`);
  const big = await noisyPicture(page, 1600, 1200, "image/png");
  expect(big.length).toBeGreaterThan(700_000);

  await page.getByTestId("logo-file").setInputFiles({ name: "photo-of-logo.png", mimeType: "image/png", buffer: big });
  await expect(page.getByTestId("logo-resized")).toBeVisible(); // told what happened to it
  await page.getByTestId("logo-upload").click();

  await expect(page.getByTestId("logo-saved")).toBeVisible();
  await expect(page.getByTestId("logo-preview")).toBeVisible();
  const [width, height] = testRow(`select width || ',' || height from organization_logos where organization_id = ${sql(world.orgId)}`).split(",").map(Number);
  expect(Math.max(width, height)).toBeLessThanOrEqual(1000);
});

test("a big JPEG works too, and something that is not a picture is explained", async ({ page, context }) => {
  world = createWorld({ label: "LogoOther" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}/settings`);

  const jpeg = await noisyPicture(page, 2400, 1800, "image/jpeg");
  expect(jpeg.length).toBeGreaterThan(700_000);
  await page.getByTestId("logo-file").setInputFiles({ name: "big.jpg", mimeType: "image/jpeg", buffer: jpeg });
  await page.getByTestId("logo-upload").click();
  await expect(page.getByTestId("logo-saved")).toBeVisible();

  await page.getByTestId("logo-file").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(page.getByTestId("logo-problem")).toContainText("picture");
  await expect(page.getByTestId("logo-upload")).toBeDisabled();
  await page.getByTestId("logo-file").setInputFiles({ name: "fake.png", mimeType: "image/png", buffer: Buffer.from("this is not an image at all") });
  await page.getByTestId("logo-upload").click();
  await expect(page.getByText(/not a readable|could not be read|picture/i).first()).toBeVisible(); // the browser or the server says so
});

test("only the owner sees the upload form", async ({ page, context }) => {
  world = createWorld({ label: "LogoRoles" });
  await signIn(context, world.addMember("admin"));
  await page.goto(`/o/${world.orgId}/settings`);
  await expect(page.getByTestId("logo-read-only")).toBeVisible();
  await expect(page.getByTestId("logo-file")).toHaveCount(0);
});
