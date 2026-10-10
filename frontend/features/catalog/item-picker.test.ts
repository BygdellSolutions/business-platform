import { beforeEach, describe, expect, it, vi } from "vitest";

import { ITEM_CHOICES, itemSearch } from "@/features/catalog/item-picker";
import { networkError } from "@/lib/api/errors";
import type { Item } from "@/lib/api/types";
import type { MoneyString, PercentString } from "@/lib/decimal";

vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const A = "00000000-0000-4000-8000-0000000000a1";
const mocked = vi.mocked(apiFetch);
const signal = new AbortController().signal;

function item(overrides: Partial<Item>): Item {
  return {
    id: "i1",
    type: "service",
    name: "Horse massage",
    description: null,
    unit: "session",
    price_ex_vat: "850.00" as MoneyString,
    price_inc_vat: "1062.50" as MoneyString,
    promotion_price_ex_vat: null,
    promotion_price_inc_vat: null,
    current_discount: null,
    sku: null,
    track_stock: false,
    low_stock_threshold: null,
    vat_rate: "25.00" as PercentString,
    active: true,
    created_at: "",
    updated_at: "",
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}

beforeEach(() => {
  mocked.mockReset();
});

describe("itemSearch", () => {
  it("asks the BFF for the active items of the organization it was made for", async () => {
    mocked.mockResolvedValue({ ok: true, status: 200, data: [] });

    await itemSearch(A)("sad", signal);

    const [orgId, path, request] = mocked.mock.calls[0];
    const url = new URL(path, "http://x");
    expect(orgId).toBe(A);
    expect(url.pathname).toBe("/items");
    expect(url.searchParams.get("active")).toBe("true");
    expect(url.searchParams.get("q")).toBe("sad");
    expect(url.searchParams.get("limit")).toBe(String(ITEM_CHOICES));
    expect(request).toEqual({ signal });
  });

  it("leaves q out when nothing was typed and cannot be widened by what was typed", async () => {
    mocked.mockResolvedValue({ ok: true, status: 200, data: [] });
    await itemSearch(A)("", signal);
    expect(
      new URL(mocked.mock.calls[0][1], "http://x").searchParams.has("q"),
    ).toBe(false);

    await itemSearch(A)("x&active=false&limit=999", signal);
    const url = new URL(mocked.mock.calls[1][1], "http://x");
    expect(url.searchParams.get("q")).toBe("x&active=false&limit=999");
    expect(url.searchParams.get("active")).toBe("true");
  });

  it("identifies each choice by the item's id; the unit and price are only a hint, as strings", async () => {
    mocked.mockResolvedValue({
      ok: true,
      status: 200,
      data: [
        item({
          id: "abc",
          name: "Saddle fitting",
          unit: "hour",
          price_ex_vat: "0.10" as MoneyString,
        }),
      ],
    });

    const result = await itemSearch(A)("", signal);

    expect(result).toEqual({
      ok: true,
      status: 200,
      data: [
        {
          id: "abc",
          label: "Saddle fitting",
          detail: "hour · 0.10",
          inactive: false,
        },
      ],
    });
  });

  it("passes a failure through unchanged", async () => {
    const failure = { ok: false as const, error: networkError() };
    mocked.mockResolvedValue(failure);
    expect(await itemSearch(A)("", signal)).toBe(failure);
  });
});
