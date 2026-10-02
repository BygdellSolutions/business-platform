/**
 * Money, VAT, quantity and decimal custom-field values are DECIMAL STRINGS everywhere in
 * the frontend: in types, form state, request payloads and rendering. They are never
 * converted to JavaScript numbers, and the frontend never does business arithmetic on them
 * (totals and VAT breakdowns are calculated by the backend and displayed as received).
 *
 * This module only checks the SHAPE of what the user typed, mirroring the backend contract
 * so obvious mistakes get instant feedback. The backend remains the authority.
 *
 * The distinct brands make it a type error to put a percentage where money is expected, or
 * a plain `number` anywhere a decimal is expected.
 *
 * There is deliberately no arithmetic here, and no `Number`, `parseFloat`, `parseInt` or
 * `Math`: ESLint forbids them in this file and in the other money-handling folders.
 */

// One brand key per kind (two different literals under one key would intersect to `never`).
declare const decimalBrand: unique symbol;
declare const moneyBrand: unique symbol;
declare const quantityBrand: unique symbol;
declare const percentBrand: unique symbol;

export type DecimalString = string & { readonly [decimalBrand]: true };
/** NUMERIC(12,2), excluding VAT, never negative. */
export type MoneyString = DecimalString & { readonly [moneyBrand]: true };
/** NUMERIC(12,3), greater than zero. */
export type QuantityString = DecimalString & { readonly [quantityBrand]: true };
/** NUMERIC(5,2), 0 to 100. */
export type PercentString = DecimalString & { readonly [percentBrand]: true };

const MONEY = /^\d{1,10}(\.\d{1,2})?$/;
const QUANTITY = /^\d{1,9}(\.\d{1,3})?$/;
const PERCENT = /^\d{1,3}(\.\d{1,2})?$/;
const CUSTOM_NUMBER = /^-?\d{1,14}(\.\d{1,4})?$/; // NUMERIC(18,4)
const ALL_ZERO = /^0+(\.0+)?$/;

export function parseMoney(input: string): MoneyString | null {
  return MONEY.test(input) ? (input as MoneyString) : null;
}

export function parseQuantity(input: string): QuantityString | null {
  return QUANTITY.test(input) && !ALL_ZERO.test(input) ? (input as QuantityString) : null;
}

/** 0 to 100 inclusive, decided on the digits alone (no numeric conversion). */
export function parsePercent(input: string): PercentString | null {
  if (!PERCENT.test(input)) return null;
  const integer = input.split(".")[0].replace(/^0+(?=\d)/, "");
  if (integer.length > 3) return null;
  if (integer.length === 3) {
    if (integer > "100") return null; // same length, so string order is numeric order
    if (integer === "100" && /[1-9]/.test(input.split(".")[1] ?? "")) return null;
  }
  return input as PercentString;
}

/** A custom-field number: up to 14 digits and 4 decimals, may be negative. */
export function parseCustomNumber(input: string): DecimalString | null {
  return CUSTOM_NUMBER.test(input) ? (input as DecimalString) : null;
}

export function isDecimalString(value: unknown): value is string {
  return typeof value === "string" && CUSTOM_NUMBER.test(value);
}
