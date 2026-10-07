import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetailList } from "@/components/ui/DetailList";
import { ItemDetails } from "@/features/catalog/ItemDetails";
import { CustomerDetails } from "@/features/customers/CustomerDetails";
import { HorseDetails } from "@/features/horses/HorseDetails";
import type { Customer, Horse, Item } from "@/lib/api/types";
import type { MoneyString, PercentString } from "@/lib/decimal";
import { EMPTY_PROFILE } from "@/lib/profile";

const ORG = "11111111-1111-4111-8111-111111111111";

const customer: Customer = {
  ...EMPTY_PROFILE,
  id: "c1",
  customer_type: "company",
  name: "Umeå HK",
  email: null,
  phone: "090-12 34 56",
  city: "Umeå",
  active: false,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

const item: Item = {
  id: "i1",
  type: "service",
  name: "Horse massage",
  description: null,
  unit: "session",
  price_ex_vat: "850.00" as MoneyString,
  vat_rate: "25.00" as PercentString,
  active: true,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

const horse: Horse = {
  id: "h1",
  name: "Kalle",
  owner_customer_id: "c2",
  stable_customer_id: "c1",
  owner: { id: "c2", name: "Anna Andersson", active: true },
  stable: { id: "c1", name: "Umeå HK", active: false },
  birth_year: 2015,
  sex: "gelding",
  breed: null,
  active: true,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

function valueOf(label: string): HTMLElement {
  const term = screen.getByText(label, { selector: "dt" });
  return term.nextElementSibling as HTMLElement;
}

function expectNoControls() {
  expect(screen.queryByRole("form")).toBeNull();
  expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  expect(screen.queryAllByRole("combobox")).toHaveLength(0);
  expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  expect(screen.queryAllByRole("button")).toHaveLength(0);
}

describe("DetailList", () => {
  it("shows a missing value as 'Not set' and keeps zero-like text", () => {
    render(<DetailList details={[{ label: "A", value: null }, { label: "B", value: "" }, { label: "C", value: "0" }]} />);

    expect(valueOf("A")).toHaveTextContent("Not set");
    expect(valueOf("B")).toHaveTextContent("Not set");
    expect(valueOf("C")).toHaveTextContent("0");
  });
});

describe("read-only records offer no controls", () => {
  it("a customer: every field, absent ones as 'Not set', and its status", () => {
    render(<CustomerDetails customer={customer} />);

    expectNoControls();
    expect(valueOf("Type")).toHaveTextContent("Company");
    expect(valueOf("Email")).toHaveTextContent("Not set");
    expect(valueOf("Phone")).toHaveTextContent("090-12 34 56");
    expect(valueOf("City")).toHaveTextContent("Umeå");
    expect(valueOf("VAT number")).toHaveTextContent("Not set");
    expect(valueOf("Status")).toHaveTextContent(/inactive/i);
  });

  it("an item: amounts exactly as the backend formatted them", () => {
    render(<ItemDetails item={item} />);

    expectNoControls();
    expect(screen.getByTestId("detail-price")).toHaveTextContent("850.00");
    expect(valueOf("VAT %")).toHaveTextContent("25.00");
    expect(valueOf("Description")).toHaveTextContent("Not set");
  });

  it("a horse: owner and stable link to their customers, an inactive one is marked", () => {
    render(<HorseDetails orgId={ORG} horse={horse} />);

    expectNoControls();
    expect(within(valueOf("Owner")).getByRole("link", { name: "Anna Andersson" })).toHaveAttribute("href", `/o/${ORG}/customers/c2`);
    expect(valueOf("Stable")).toHaveTextContent("(inactive)");
    expect(valueOf("Sex")).toHaveTextContent("Gelding");
    expect(valueOf("Birth year")).toHaveTextContent("2015");
    expect(valueOf("Breed")).toHaveTextContent("Not set");
  });

  it("a horse without a stable says so", () => {
    render(<HorseDetails orgId={ORG} horse={{ ...horse, stable: null, stable_customer_id: null }} />);

    expect(valueOf("Stable")).toHaveTextContent("Not set");
  });
});
