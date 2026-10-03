import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

/**
 * Money, VAT, quantity and decimal custom-field values stay STRINGS (see lib/decimal.ts).
 * In the folders that handle them, nothing may turn a value into a JavaScript number or
 * round it. This is scoped on purpose: parseFloat, Number and Math are fine elsewhere
 * (page numbers, indexes, timestamps), but not where a decimal could pass through.
 */
export const DECIMAL_ZONES = [
  "lib/decimal.ts",
  "components/ui/DecimalText.tsx",
  "components/ui/Field.tsx",
  "components/custom-fields/**/*.{ts,tsx}",
  "features/catalog/**/*.{ts,tsx}",
  "app/o/*/catalog/**/*.{ts,tsx}",
  "features/transactions/**/*.{ts,tsx}",
];

const WHY = "Decimal values are strings end to end; never convert them to JavaScript numbers.";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: DECIMAL_ZONES,
    ignores: ["**/*.test.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        { selector: "CallExpression[callee.name=/^(Number|parseFloat|parseInt)$/]", message: WHY },
        { selector: "NewExpression[callee.name='Number']", message: WHY },
        { selector: "CallExpression[callee.object.name='Number']", message: WHY },
        { selector: "CallExpression[callee.object.name='Math']", message: `${WHY} (no rounding either)` },
        { selector: "UnaryExpression[operator='+']", message: `${WHY} (unary plus converts to a number)` },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "test-results/**",
    "playwright-report/**",
  ]),
]);

export default eslintConfig;
