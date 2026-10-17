import { describe, expect, expectTypeOf, it } from "vitest";

import { isDecimalString, parseCustomNumber, parseMoney, parsePercent, parseQuantity, trimQuantity, type DecimalString, type MoneyString, type PercentString, type QuantityString } from "@/lib/decimal";

describe("money (shape only: digits with an optional decimal part)", () => {
  it.each(["0", "0.00", "0.1", "0.01", "850.00", "850", "19.99", "9999999999.99", "0000012.5"])("accepts %s and returns the same string", (input) => {
    const money = parseMoney(input);
    expect(money).toBe(input);
    expect(typeof money).toBe("string");
  });

  it.each(["", " ", "-1", "-0.01", "+1", "1e2", "1E2", "NaN", "Infinity", "1,5", " 1", "1 ", "1.", ".5", "abc", "0x10", "١٢٣"])("rejects %j: not a decimal at all", (input) => {
    expect(parseMoney(input)).toBeNull();
  });

  it.each(["1.001", "10000000000.00", "123456789012345678901234567890.123456789"])(
    "does not judge %j: precision and range belong to the backend, which answers 422 on the field",
    (input) => {
      expect(parseMoney(input)).toBe(input);
    },
  );
});

describe("quantity (shape only)", () => {
  it.each(["1", "0.001", "2.375", "0.25", "100", "999999999.999", "0", "0.000", "1.0001", "1000000000"])("accepts %s unchanged", (input) => {
    expect(parseQuantity(input)).toBe(input);
  });

  it.each(["-1", "1e1", "", "abc"])("rejects %j", (input) => {
    expect(parseQuantity(input)).toBeNull();
  });
});

describe("percent (shape only)", () => {
  it.each(["0", "0.00", "6", "12.5", "25", "25.00", "99.99", "100", "100.00", "010", "007", "100.01", "101", "999", "25.555"])("accepts %s: the 0 to 100 range is the backend's rule", (input) => {
    expect(parsePercent(input)).toBe(input);
  });

  it.each(["-1", "1e1", "", "25%"])("rejects %j", (input) => {
    expect(parsePercent(input)).toBeNull();
  });
});

describe("custom numbers", () => {
  it.each(["12.5", "-3", "0", "0.0001", "99999999999999.9999", "-99999999999999.9999"])("accepts %s", (input) => {
    expect(parseCustomNumber(input)).toBe(input);
    expect(isDecimalString(input)).toBe(true);
  });

  it.each(["1e2", "NaN", "", " 1", "--1", "1,5"])("rejects %j", (input) => {
    expect(parseCustomNumber(input)).toBeNull();
    expect(isDecimalString(input)).toBe(false);
  });

  it("only strings are decimal values: a number is never one", () => {
    expect(isDecimalString(12.5)).toBe(false);
    expect(isDecimalString(null)).toBe(false);
    expect(isDecimalString(undefined)).toBe(false);
  });
});

describe("values that floating point cannot represent survive unchanged", () => {
  it.each(["0.1", "0.2", "0.30", "4.35", "8.20", "1.15", "9999999999.99", "123456.78"])("%s", (input) => {
    const money = parseMoney(input);
    expect(money).toBe(input); // not "4.3499999999999996", not 8.2
    expect(JSON.stringify({ price_ex_vat: money })).toBe(`{"price_ex_vat":"${input}"}`);
    expect(JSON.parse(JSON.stringify({ price_ex_vat: money })).price_ex_vat).toBeTypeOf("string");
  });
});

describe("types: a decimal is a branded string, never a number", () => {
  it("is distinct per kind", () => {
    const money = parseMoney("1.00") as MoneyString;
    const percent = parsePercent("25") as PercentString;
    const quantity = parseQuantity("1") as QuantityString;

    expectTypeOf(money).toExtend<DecimalString>();
    expectTypeOf(money).toExtend<string>();
    expectTypeOf(percent).not.toExtend<MoneyString>();
    expectTypeOf(quantity).not.toExtend<PercentString>();
    expectTypeOf(money).not.toBeNumber();

    function takesMoney(value: MoneyString) {
      return value;
    }
    takesMoney(money);
    // @ts-expect-error a number is not money
    takesMoney(1.5);
    // @ts-expect-error a plain string has not been validated
    takesMoney("1.00");
    // @ts-expect-error a percentage is not money
    takesMoney(percent);
    // @ts-expect-error a quantity is not money
    takesMoney(quantity);
  });

  it("parse results are the only way to obtain one", () => {
    expectTypeOf(parseMoney).returns.toEqualTypeOf<MoneyString | null>();
    expectTypeOf(parseQuantity).returns.toEqualTypeOf<QuantityString | null>();
    expectTypeOf(parsePercent).returns.toEqualTypeOf<PercentString | null>();
    expectTypeOf(parseCustomNumber).returns.toEqualTypeOf<DecimalString | null>();
  });
});

describe("trimQuantity", () => {
  it("drops only the zeros that pad the fraction, as text", () => {
    expect(["10.000", "2.500", "0.125", "-3.000", "0.000", "1200", "abc"].map(trimQuantity)).toEqual(["10", "2.5", "0.125", "-3", "0", "1200", "abc"]);
  });
});
