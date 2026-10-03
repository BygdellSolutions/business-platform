// @vitest-environment node
import { ESLint } from "eslint";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { DECIMAL_ZONES } from "../eslint.config.mjs";

const root = path.resolve(__dirname, "..");
const eslint = new ESLint({ cwd: root });

async function restricted(code: string, file: string) {
  const [result] = await eslint.lintText(code, { filePath: path.join(root, file) });
  return result.messages.filter((m) => m.ruleId === "no-restricted-syntax");
}

const CONVERSIONS = [
  "const n = Number(value);",
  "const n = parseFloat(value);",
  "const n = parseInt(value, 10);",
  "const n = Number.parseFloat(value);",
  "const n = new Number(value);",
  "const n = +value;",
  "const n = Math.round(value);",
  "const n = Math.floor(value);",
];

const IN_ZONE = [
  "lib/decimal.ts",
  "components/ui/DecimalText.tsx",
  "components/custom-fields/Anything.tsx",
  "features/transactions/LineEditor.tsx",
  "features/catalog/ItemForm.tsx",
  "components/ui/Field.tsx",
  "app/o/[orgId]/catalog/page.tsx",
  "app/o/[orgId]/catalog/[id]/page.tsx",
  "app/o/[orgId]/transactions/page.tsx",
  "app/o/[orgId]/transactions/[id]/page.tsx",
  "features/transactions/TransactionEditor.tsx",
  "features/transactions/AddLineForm.tsx",
  "features/transactions/TotalsPanel.tsx",
  "features/invoices/InvoiceDocument.tsx",
  "features/invoices/InvoiceCreateForm.tsx",
  "features/invoices/eligibility.ts",
  "app/o/[orgId]/invoices/page.tsx",
  "app/o/[orgId]/invoices/[invoiceId]/page.tsx",
  "app/o/[orgId]/invoices/new/page.tsx",
  "components/snapshots/FieldSnapshots.tsx",
];

describe("decimal-critical folders may not convert decimals to numbers", () => {
  it("covers the money-handling folders", () => {
    expect(DECIMAL_ZONES).toEqual(expect.arrayContaining(["lib/decimal.ts", "components/custom-fields/**/*.{ts,tsx}", "features/transactions/**/*.{ts,tsx}", "features/invoices/**/*.{ts,tsx}", "app/o/*/invoices/**/*.{ts,tsx}", "components/snapshots/**/*.{ts,tsx}"]));
  });

  for (const file of IN_ZONE) {
    it.each(CONVERSIONS)(`${file}: forbids %s`, async (code) => {
      const messages = await restricted(`export function f(value: string) { ${code} return n; }`, file);
      expect(messages.length).toBeGreaterThan(0);
      expect(messages[0].message).toContain("strings");
    });
  }

  it("allows ordinary string handling in the same files", async () => {
    const code = "export function f(value: string) { return value.trim().padStart(4, '0') + String(value.length); }";
    for (const file of IN_ZONE) expect(await restricted(code, file)).toEqual([]);
  });
});

describe("the rule is scoped, not a global ban", () => {
  it.each(["lib/api/client.ts", "components/shell/OrgSwitcher.tsx", "features/dashboard/CustomerPreview.tsx", "app/page.tsx"])("%s may use Number, parseInt and Math", async (file) => {
    for (const code of CONVERSIONS) {
      expect(await restricted(`export function f(value: string) { ${code} return n; }`, file)).toEqual([]);
    }
  });
});
