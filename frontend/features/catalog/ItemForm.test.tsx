import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { ItemForm } from "@/features/catalog/ItemForm";
import { normalizeError, type ApiResult } from "@/lib/api/errors";
import type { Item } from "@/lib/api/types";
import type { MoneyString, PercentString } from "@/lib/decimal";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const A = "00000000-0000-4000-8000-0000000000a1";
const B = "00000000-0000-4000-8000-0000000000b2";
const mocked = vi.mocked(apiFetch);

function item(overrides: Partial<Item> = {}): Item {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    type: "service",
    name: "Horse massage",
    description: null,
    unit: "hour",
    price_ex_vat: "850.00" as MoneyString,
    current_discount: null,
    sku: null,
    track_stock: false,
    vat_rate: "25.00" as PercentString,
    active: true,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}
const ok = <T,>(data: T, status = 200): ApiResult<T> => ({ ok: true, status, data });
const fail = <T,>(status: number, body: unknown): ApiResult<T> => ({ ok: false, error: normalizeError(status, body) });

function mount(orgId: string, existing?: Item) {
  return render(
    <OrgScope orgId={orgId}>
      <ItemForm item={existing} />
    </OrgScope>,
  );
}

async function fillNew(price: string, vat: string) {
  await userEvent.type(screen.getByLabelText("Name"), "Saddle fitting");
  await userEvent.type(screen.getByLabelText("Unit"), "hour");
  await userEvent.type(screen.getByLabelText("Price excluding VAT"), price);
  await userEvent.type(screen.getByLabelText("VAT rate (%)"), vat);
}

beforeEach(() => {
  mocked.mockReset();
  router.push.mockReset();
  router.refresh.mockReset();
});

describe("decimals are strings from the input to the request body", () => {
  it.each([
    ["0.10", "8.20"],
    ["4.35", "0.10"],
    ["8.20", "25"],
    ["9999999999.99", "100"],
    ["0.30", "6.50"],
    ["850", "25.00"],
  ])("price %s and VAT %s are sent exactly as typed, as JSON strings", async (price, vat) => {
    mocked.mockResolvedValue(ok(item({ id: "new-id" }), 201));
    mount(A);

    await fillNew(price, vat);
    await userEvent.click(screen.getByRole("button", { name: "Create item" }));

    expect(mocked).toHaveBeenCalledTimes(1);
    const [orgId, path, request] = mocked.mock.calls[0];
    const body = request?.body as Record<string, unknown>;
    expect(orgId).toBe(A);
    expect(path).toBe("/items");
    expect(body.price_ex_vat).toBe(price);
    expect(body.vat_rate).toBe(vat);
    expect(typeof body.price_ex_vat).toBe("string");
    expect(typeof body.vat_rate).toBe("string");
    // What goes over the wire: quoted strings, so no float parsing can happen on the way.
    const wire = JSON.stringify(body);
    expect(wire).toContain(`"price_ex_vat":"${price}"`);
    expect(wire).toContain(`"vat_rate":"${vat}"`);
    expect(wire).not.toContain("organization");
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/o/${A}/catalog/new-id?created=1`));
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("sends no number anywhere in the body", async () => {
    mocked.mockResolvedValue(ok(item(), 201));
    mount(A);
    await fillNew("8.20", "25");

    await userEvent.click(screen.getByRole("button", { name: "Create item" }));

    const body = mocked.mock.calls[0][2]?.body as Record<string, unknown>;
    expect(Object.values(body).filter((value) => typeof value === "number")).toEqual([]);
    expect(body).toEqual({ type: "service", name: "Saddle fitting", description: null, unit: "hour", price_ex_vat: "8.20", vat_rate: "25", active: true, sku: null, track_stock: false });
  });

  it("shows the saved record exactly as the backend formatted it", async () => {
    mocked.mockResolvedValue(ok(item({ price_ex_vat: "8.20" as MoneyString, vat_rate: "0.10" as PercentString })));
    mount(A, item());

    await userEvent.clear(screen.getByLabelText("Price excluding VAT"));
    await userEvent.type(screen.getByLabelText("Price excluding VAT"), "8.2");
    await userEvent.clear(screen.getByLabelText("VAT rate (%)"));
    await userEvent.type(screen.getByLabelText("VAT rate (%)"), "0.1");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ price_ex_vat: "8.2", vat_rate: "0.1" });
    await waitFor(() => expect(screen.getByLabelText("Price excluding VAT")).toHaveValue("8.20"));
    expect(screen.getByLabelText("VAT rate (%)")).toHaveValue("0.10");
  });

  it("loads an existing price into the control without reformatting it", () => {
    mount(A, item({ price_ex_vat: "9999999999.99" as MoneyString, vat_rate: "6.00" as PercentString }));
    expect(screen.getByLabelText("Price excluding VAT")).toHaveValue("9999999999.99");
    expect(screen.getByLabelText("VAT rate (%)")).toHaveValue("6.00");
  });
});

describe("only the shape of a decimal is checked locally", () => {
  it.each(["", "abc", "1,5", "1e2", "-1", "1.", ".5", "1 000"])("%j is not sent: it is not a decimal at all", async (typed) => {
    mount(A);
    await userEvent.type(screen.getByLabelText("Name"), "Saddle fitting");
    await userEvent.type(screen.getByLabelText("Unit"), "hour");
    if (typed !== "") await userEvent.type(screen.getByLabelText("Price excluding VAT"), typed); // typing "" is not an event
    await userEvent.type(screen.getByLabelText("VAT rate (%)"), "25");

    await userEvent.click(screen.getByRole("button", { name: "Create item" }));

    expect(mocked).not.toHaveBeenCalled();
    expect(screen.getByTestId("error-price_ex_vat")).toHaveTextContent("Enter a number such as 850.00");
    expect(screen.queryByTestId("error-vat_rate")).toBeNull();
  });

  it.each([
    ["10000000000.00", "25"], // too many digits
    ["1.005", "25"], // too many decimals
    ["10", "100.01"], // VAT above 100
    ["10", "999"],
  ])("price %s / VAT %s is NOT judged by the frontend: the backend decides", async (price, vat) => {
    mocked.mockResolvedValue(fail(422, { detail: [{ loc: ["body", "price_ex_vat"], msg: "Value error, whatever the backend says", type: "x" }] }));
    mount(A);
    await fillNew(price, vat);

    await userEvent.click(screen.getByRole("button", { name: "Create item" }));

    expect(mocked).toHaveBeenCalledTimes(1);
    expect((mocked.mock.calls[0][2]?.body as Record<string, unknown>).price_ex_vat).toBe(price);
    expect(await screen.findByTestId("error-price_ex_vat")).toHaveTextContent("whatever the backend says");
  });

  it("maps 422 locations to the matching controls, price and VAT included", async () => {
    mocked.mockResolvedValue(
      fail(422, {
        detail: [
          { loc: ["body", "price_ex_vat"], msg: "Value error, must be a non-negative decimal within the allowed precision", type: "value_error" },
          { loc: ["body", "vat_rate"], msg: "Input should be less than or equal to 100", type: "less_than_equal" },
          { loc: ["body", "unit"], msg: "String should have at most 32 characters", type: "string_too_long" },
          { loc: ["body", "type"], msg: "Input should be 'service' or 'product'", type: "enum" },
        ],
      }),
    );
    mount(A);
    await fillNew("1.005", "101");

    await userEvent.click(screen.getByRole("button", { name: "Create item" }));

    expect(await screen.findByTestId("error-price_ex_vat")).toHaveTextContent("must be a non-negative decimal within the allowed precision");
    expect(screen.getByTestId("error-vat_rate")).toHaveTextContent("less than or equal to 100");
    expect(screen.getByTestId("error-unit")).toHaveTextContent("at most 32");
    expect(screen.getByTestId("error-type")).toBeInTheDocument();
    expect(screen.queryByTestId("error-name")).toBeNull();
    expect(screen.getByLabelText("Price excluding VAT")).toHaveValue("1.005"); // the draft is kept
  });

  it("a local shape error is cleared on the next submit", async () => {
    mocked.mockResolvedValue(ok(item({ id: "x" }), 201));
    mount(A);
    await fillNew("1,5", "25");
    await userEvent.click(screen.getByRole("button", { name: "Create item" }));
    expect(screen.getByTestId("error-price_ex_vat")).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Price excluding VAT"));
    await userEvent.type(screen.getByLabelText("Price excluding VAT"), "1.50");
    await userEvent.click(screen.getByRole("button", { name: "Create item" }));

    expect(screen.queryByTestId("error-price_ex_vat")).toBeNull();
    expect(mocked).toHaveBeenCalledTimes(1);
  });
});

describe("editing an item", () => {
  it("sends only what changed, comparing decimals as strings", async () => {
    mocked.mockResolvedValue(ok(item({ name: "Deep tissue massage" })));
    mount(A, item());

    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Deep tissue massage");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked).toHaveBeenCalledWith(A, "/items/22222222-2222-4222-8222-222222222222", { method: "PATCH", body: { name: "Deep tissue massage" } });
  });

  it("makes no request when nothing changed", async () => {
    mount(A, item());
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(mocked).not.toHaveBeenCalled();
    expect(screen.getByTestId("unchanged")).toBeInTheDocument();
  });

  it("writing the same price differently is a change sent to the backend, which normalizes it", async () => {
    mocked.mockResolvedValue(ok(item()));
    mount(A, item());

    await userEvent.clear(screen.getByLabelText("Price excluding VAT"));
    await userEvent.type(screen.getByLabelText("Price excluding VAT"), "850");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ price_ex_vat: "850" });
  });

  it("clearing the description sends null", async () => {
    mocked.mockResolvedValue(ok(item({ description: null })));
    mount(A, item({ description: "Relaxing" }));

    await userEvent.clear(screen.getByLabelText("Description"));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ description: null });
  });

  it("deactivates and reactivates through the shared toggle", async () => {
    mocked.mockResolvedValueOnce(ok(item({ active: false })));
    mount(A, item());

    await userEvent.click(screen.getByRole("button", { name: "Deactivate item" }));

    expect(mocked).toHaveBeenCalledWith(A, "/items/22222222-2222-4222-8222-222222222222", { method: "PATCH", body: { active: false } });
    expect(await screen.findByRole("button", { name: "Reactivate item" })).toBeInTheDocument();
  });
});

describe("organization scope", () => {
  it("drops an unsaved price when the organization changes", async () => {
    const { rerender } = mount(A);
    await fillNew("4.35", "25");
    expect(screen.getByLabelText("Price excluding VAT")).toHaveValue("4.35");

    rerender(
      <OrgScope orgId={B}>
        <ItemForm />
      </OrgScope>,
    );

    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText("Price excluding VAT")).toHaveValue("");
  });
});

describe("article number and stock tracking", () => {
  it("offers stock tracking only for a product, and sends it with the article number", async () => {
    mocked.mockResolvedValue(ok(item(), 201));
    mount(A);
    expect(screen.queryByLabelText("Track stock")).toBeNull(); // a service never holds stock

    await userEvent.selectOptions(screen.getByLabelText("Type"), "product");
    await userEvent.type(screen.getByLabelText("Article number (SKU)"), "LIN-01");
    await userEvent.click(screen.getByLabelText("Track stock"));
    await fillNew("120", "25");
    await userEvent.click(screen.getByRole("button", { name: "Create item" }));

    expect(mocked.mock.calls[0][2]?.body).toMatchObject({ type: "product", sku: "LIN-01", track_stock: true });
  });

  it("turning a stock-tracking product into a service stops tracking in the same save", async () => {
    mocked.mockResolvedValue(ok(item({ type: "service", track_stock: false })));
    mount(A, item({ type: "product", track_stock: true, sku: "LIN-01" }));

    await userEvent.selectOptions(screen.getByLabelText("Type"), "service");
    expect(screen.queryByLabelText("Track stock")).toBeNull();
    await userEvent.clear(screen.getByLabelText("Article number (SKU)"));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(mocked.mock.calls[0][2]?.body).toEqual({ type: "service", sku: null, track_stock: false });
  });
});
