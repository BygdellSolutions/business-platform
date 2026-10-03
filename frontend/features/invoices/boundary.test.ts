// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { imports, violationsOf } from "../../test-support/genericity";

/**
 * An invoice is a stored document, so the code that shows it may only ever ask the Invoicing API
 * for content. This keeps that true mechanically:
 *
 *   - the invoice feature and the invoice pages import no customer, catalog, horse or editable
 *     custom-field code, and never request those records;
 *   - the only requests they make go to /invoices, /invoiceable-transactions and, for navigation
 *     and eligibility, /transactions (the create screen) - never /customers, /items, /horses or
 *     /custom-fields (the list page's customer FILTER is the one exception, and it is a filter
 *     label, not invoice content);
 *   - the document itself (InvoiceDocument) fetches nothing at all.
 */
const ROOT = path.resolve(__dirname, "..", "..");

function sourceFiles(directory: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(directory)) {
    const full = path.join(directory, entry);
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full));
    else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry) && entry !== "testing.tsx") out.push(full);
  }
  return out;
}

const FEATURE = sourceFiles(path.join(ROOT, "features/invoices"));
const PAGES = sourceFiles(path.join(ROOT, "app/o/[orgId]/invoices"));
const rel = (file: string) => path.relative(ROOT, file).replaceAll("\\", "/");

const LIVE_IMPORTS = /^@\/(features\/(customers|catalog|horses|transactions|dashboard)|components\/custom-fields|lib\/custom-fields)/;
const LIVE_REQUESTS = /["'`]\/(api\/)?(customers|items|horses|custom-fields)\b/;

describe("the invoice code reads invoice content only from the Invoicing API", () => {
  it("covers the feature and the three pages", () => {
    expect(FEATURE.map(rel)).toEqual(expect.arrayContaining(["features/invoices/InvoiceDocument.tsx", "features/invoices/InvoiceView.tsx", "features/invoices/InvoiceCreateForm.tsx"]));
    expect(PAGES.map(rel).sort()).toEqual(["app/o/[orgId]/invoices/[invoiceId]/page.tsx", "app/o/[orgId]/invoices/new/page.tsx", "app/o/[orgId]/invoices/page.tsx"]);
  });

  it.each(FEATURE.map((file) => [rel(file), file]))("%s imports no live-record code and requests no live records", (_name, file) => {
    const source = readFileSync(file, "utf8");
    for (const specifier of imports(source)) expect(specifier).not.toMatch(LIVE_IMPORTS);
    expect(source).not.toMatch(LIVE_REQUESTS);
  });

  it.each(PAGES.map((file) => [rel(file), file]))("%s requests no live records for document content", (name, file) => {
    const source = readFileSync(file, "utf8");
    if (name === "app/o/[orgId]/invoices/page.tsx") {
      // The list reads the customer only to LABEL its customer filter (the picker's initial value).
      expect(source.match(/serverReadOrNull<Customer>/g)?.length ?? 0).toBeLessThanOrEqual(1);
      expect(source).not.toMatch(/\/api\/(items|horses|custom-fields)/);
    } else {
      expect(source).not.toMatch(LIVE_REQUESTS);
      expect(source).not.toMatch(/\/api\/(customers|items|horses|custom-fields)/);
      for (const specifier of imports(source)) expect(specifier).not.toMatch(LIVE_IMPORTS);
    }
  });

  it("the document and the status badge fetch nothing and hold no state", () => {
    for (const name of ["InvoiceDocument.tsx", "InvoiceStatusBadge.tsx"]) {
      const source = readFileSync(path.join(ROOT, "features/invoices", name), "utf8");
      expect(source).not.toMatch(/apiFetch|serverRead|fetch\(|useEffect|useState|useRouter|useOrgId/);
    }
  });

  it("the customer name shown comes from the invoice, not a lookup: the list and the heading use customer_name", () => {
    const list = readFileSync(path.join(ROOT, "app/o/[orgId]/invoices/page.tsx"), "utf8");
    expect(list).toContain("invoice.customer_name");
    const detail = readFileSync(path.join(ROOT, "app/o/[orgId]/invoices/[invoiceId]/page.tsx"), "utf8");
    expect(detail).toContain("invoice.customer_name");
  });
});

describe("the invoice code does no arithmetic on amounts", () => {
  const SOURCES = [...FEATURE, ...PAGES].map((file) => [rel(file), readFileSync(file, "utf8")] as const);

  it.each(SOURCES)("%s converts nothing to a number and does no money maths", (_name, source) => {
    // Comments and string literals (test ids such as "line-quantity", class names) are not code.
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/.*$/gm, "$1")
      .replace(/"[^"\n]*"/g, '""')
      .replace(/'[^'\n]*'/g, "''");
    expect(code).not.toMatch(/\b(Number|parseFloat|parseInt)\s*\(/);
    expect(code).not.toMatch(/\bMath\./);
    expect(code).not.toMatch(/\b(reduce|toFixed|toLocaleString|Intl\.NumberFormat)\b/);
    // No arithmetic between amount fields (net/vat/gross/price/quantity) anywhere.
    expect(code).not.toMatch(/(net_amount|vat_amount|gross_amount|unit_price_ex_vat|quantity|vat_rate)\s*[-+*/]\s*[\w(]/);
    expect(code).not.toMatch(/[\w)]\s*[-+*/]\s*[\w.]*(net_amount|vat_amount|gross_amount|unit_price_ex_vat|quantity|vat_rate)\b/);
  });
});

describe("the check itself (so a weak check cannot pass silently)", () => {
  it.each([
    'import { customerSearch } from "@/features/customers/customer-picker";',
    'import { CustomFieldsPanel } from "@/components/custom-fields/CustomFieldsPanel";',
    'import { readEntityFields } from "@/lib/custom-fields/server";',
  ])("flags the live import %#", (source) => {
    expect(imports(source).some((specifier) => LIVE_IMPORTS.test(specifier))).toBe(true);
  });

  it.each(['apiFetch(orgId, "/customers/" + id)', 'serverRead(orgId, "/api/items")', 'apiFetch(orgId, `/horses?x=1`)', 'fetch("/custom-fields/definitions")'])("flags the live request %#", (source) => {
    expect(LIVE_REQUESTS.test(source)).toBe(true);
  });

  it("the genericity checker still rejects what it should", () => {
    expect(violationsOf('import x from "@/features/customers/a";')).not.toEqual([]);
  });
});
