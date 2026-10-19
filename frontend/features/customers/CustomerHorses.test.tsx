import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Horse } from "@/lib/api/types";
import { CustomerHorses, HORSES_SHOWN, horsesOfCustomer } from "./CustomerHorses";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const CUSTOMER = "00000000-0000-4000-8000-0000000000c1";

function horse(number: number, name: string, overrides: Partial<Horse> = {}): Horse {
  return {
    id: `0000000${number}-0000-4000-8000-000000000000`, number, name, owner_customer_id: CUSTOMER, stable_customer_id: null,
    owner: { id: CUSTOMER, name: "Anna", active: true }, stable: null, birth_year: null, sex: null, breed: null, active: true,
    created_at: "", updated_at: "", created_by: null, updated_by: null, ...overrides,
  };
}

describe("horsesOfCustomer", () => {
  it("lists a horse once, by name, and says in what way it is the customer's", () => {
    const both = horse(3, "Bella");
    const rows = horsesOfCustomer([horse(2, "Storm"), both], [horse(1, "Kalle"), both]);
    expect(rows.map((row) => [row.horse.name, row.owner, row.stable])).toEqual([
      ["Bella", true, true],
      ["Kalle", false, true],
      ["Storm", true, false],
    ]);
  });

  it("sorts names the Swedish way and keeps horses of the same name apart by number", () => {
    const rows = horsesOfCustomer([horse(5, "Örn"), horse(2, "Åsa"), horse(4, "Zorro"), horse(3, "Kalle"), horse(1, "Kalle")], []);
    expect(rows.map((row) => `${row.horse.name} ${row.horse.number}`)).toEqual(["Kalle 1", "Kalle 3", "Zorro 4", "Åsa 2", "Örn 5"]);
  });
});

const mount = (owned: Horse[], stabled: Horse[], canWrite = true) =>
  render(<CustomerHorses orgId={ORG} customerId={CUSTOMER} owned={owned} stabled={stabled} canWrite={canWrite} />);

describe("the Horses section of a customer", () => {
  it("says so when the customer has no horses", () => {
    mount([], []);
    expect(screen.getByTestId("no-horses").textContent).toContain("No horses");
    expect(screen.queryByTestId("customer-horse-row")).toBeNull();
  });

  it("shows each horse with its number, a link to it, the role, sex and breed, and marks an inactive one", () => {
    mount([horse(2, "Storm", { sex: "gelding", breed: "Swedish warmblood" }), horse(4, "Old Timer", { active: false })], [horse(1, "Kalle", { stable_customer_id: CUSTOMER })]);
    const rows = screen.getAllByTestId("customer-horse-row");
    expect(rows.map((row) => within(row).getByTestId("customer-horse-link").textContent)).toEqual(["Kalle", "Old Timer", "Storm"]);
    expect(within(rows[0]).getByTestId("customer-horse-role").textContent).toBe("Stable");
    expect(within(rows[2]).getByTestId("customer-horse-role").textContent).toBe("Owner");
    expect(rows[2].textContent).toContain("gelding");
    expect(rows[2].textContent).toContain("Swedish warmblood");
    expect(rows[1].textContent).toContain("(inactive)");
    expect(within(rows[0]).getByTestId("customer-horse-link").getAttribute("href")).toBe(`/o/${ORG}/horses/00000001-0000-4000-8000-000000000000`);
    expect(screen.getByText("Horses").closest("h2")?.textContent).toContain("(3)"); // the count shows while the section is closed
  });

  it("offers the filtered horse list only for the kind that has more than is shown", () => {
    const many = Array.from({ length: HORSES_SHOWN + 1 }, (_, index) => horse(index + 1, `Horse ${String(index).padStart(2, "0")}`));
    mount(many, []);
    expect(screen.getAllByTestId("customer-horse-row")).toHaveLength(HORSES_SHOWN);
    expect(screen.getByTestId("all-owned-horses").getAttribute("href")).toBe(`/o/${ORG}/horses?owner_customer_id=${CUSTOMER}`);
    expect(screen.queryByTestId("all-stabled-horses")).toBeNull();
  });

  it("offers New horse, with this customer as the owner, only to someone who may write records", () => {
    const { unmount } = mount([], []);
    expect(screen.getByTestId("new-customer-horse").getAttribute("href")).toBe(`/o/${ORG}/horses/new?owner_customer_id=${CUSTOMER}`);
    unmount();
    mount([], [], false);
    expect(screen.queryByTestId("new-customer-horse")).toBeNull();
  });
});
