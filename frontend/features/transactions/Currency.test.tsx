import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Harness, installBackend, ok, resetServer, router, tx } from "@/features/transactions/testing";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(tx());
  installBackend(() => ok(null));
});

describe("the currency of a transaction", () => {
  it("is shown with the totals exactly as the server sent it", () => {
    render(<Harness initial={tx({ currency: "EUR" })} />);
    expect(screen.getByTestId("total-currency")).toHaveTextContent("EUR");
  });

  it("is never assumed: a transaction that predates currencies says so", () => {
    render(<Harness initial={tx({ currency: null })} />);
    expect(screen.getByTestId("total-currency")).toHaveTextContent("No currency recorded");
    expect(screen.getByTestId("total-currency")).not.toHaveTextContent("SEK");
  });

  it("is not editable: there is no control for it", () => {
    render(<Harness initial={tx({ currency: "SEK" })} />);
    expect(screen.queryByLabelText(/currency/i)).toBeNull();
  });
});
