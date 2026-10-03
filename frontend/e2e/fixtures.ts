import { test as base, type Page } from "@playwright/test";

export * from "@playwright/test";

/**
 * Wait until React has attached itself to the page's interactive elements.
 *
 * A server-rendered page is visible, and its buttons and fields can be clicked, a moment BEFORE
 * React has hydrated it. A click or a submit in that window does nothing (or, for a form, submits
 * natively), which made tests that act immediately after loading a page fail now and then on a
 * busy machine. React marks every element it has hydrated with a `__reactProps$...` property, so
 * "hydrated" means: every link, button and field in the page content has one.
 *
 * Only a full page load needs this (goto, reload, back/forward); client-side navigations keep the
 * already-running React. A page that never settles is not an error here: the test's own
 * assertions then say what is wrong.
 */
export async function hydrated(page: Page): Promise<void> {
  await page
    .waitForFunction(
      () => {
        const root = document.querySelector("main") ?? document.body;
        const elements = Array.from(root.querySelectorAll("a, button, input, select, textarea"));
        return elements.every((element) => Object.keys(element).some((key) => key.startsWith("__reactProps$")));
      },
      undefined,
      { timeout: 15_000 },
    )
    .catch(() => undefined);
}

const FULL_LOADS = ["goto", "reload", "goBack", "goForward"] as const;

/** Make a page wait for hydration after each full load. Also usable for pages a test creates itself. */
export function waitForHydration(page: Page): Page {
  for (const name of FULL_LOADS) {
    const original = page[name].bind(page) as (...args: unknown[]) => Promise<unknown>;
    (page as unknown as Record<string, unknown>)[name] = async (...args: unknown[]) => {
      const result = await original(...args);
      await hydrated(page);
      return result;
    };
  }
  return page;
}

export const test = base.extend({
  context: async ({ context }, provide) => {
    context.on("page", (page) => void waitForHydration(page)); // pages opened with context.newPage()
    await provide(context);
  },
  page: async ({ page }, provide) => {
    await provide(waitForHydration(page));
  },
});
