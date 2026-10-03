/**
 * Money, VAT, quantity and decimal custom-field values are DECIMAL STRINGS everywhere in
 * the frontend: in types, form state, request payloads and rendering. They are never
 * converted to JavaScript numbers, and the frontend never does business arithmetic on them
 * (totals and VAT breakdowns are calculated by the backend and displayed as received).
 *
 * This module only checks the SHAPE of what the user typed (digits with an optional decimal
 * part), so a value that is not a decimal at all ("abc", "1,5", "1e2") never gets typed as
 * one. It does NOT repeat the backend's rules: the number of digits, the allowed range
 * (0 to 100 for a percentage, greater than zero for a quantity) and similar limits belong to
 * the backend, whose 422 answer is shown on the field. Duplicated limits would drift.
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

const UNSIGNED_DECIMAL = /^\d+(\.\d+)?$/;
const SIGNED_DECIMAL = /^-?\d+(\.\d+)?$/;

/** Money is written as digits with an optional decimal part; the backend decides the limits. */
export function parseMoney(input: string): MoneyString | null {
  return UNSIGNED_DECIMAL.test(input) ? (input as MoneyString) : null;
}

export function parseQuantity(input: string): QuantityString | null {
  return UNSIGNED_DECIMAL.test(input) ? (input as QuantityString) : null;
}

export function parsePercent(input: string): PercentString | null {
  return UNSIGNED_DECIMAL.test(input) ? (input as PercentString) : null;
}

/** A custom-field number: may be negative. */
export function parseCustomNumber(input: string): DecimalString | null {
  return SIGNED_DECIMAL.test(input) ? (input as DecimalString) : null;
}

export function isDecimalString(value: unknown): value is string {
  return typeof value === "string" && SIGNED_DECIMAL.test(value);
}
