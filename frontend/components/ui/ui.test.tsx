import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DecimalText } from "@/components/ui/DecimalText";
import { parseMoney, parsePercent, parseQuantity } from "@/lib/decimal";
import PageError from "@/app/o/[orgId]/error";
import RootError from "@/app/error";
import NotFound from "@/app/not-found";
import Loading from "@/app/o/[orgId]/loading";

describe("DecimalText renders exactly what it is given", () => {
  it.each(["850.00", "0.1", "0.10", "9999999999.99", "0.001", "100.00", "123456.78", "-3", "99999999999999.9999"])("%s", (value) => {
    const { container } = render(<DecimalText value={value} />);
    expect(container.textContent).toBe(value);
  });

  it("keeps branded decimals as strings in the DOM (no rounding, no locale formatting, no trailing-zero loss)", () => {
    const { container } = render(
      <p>
        <DecimalText value={parseMoney("850.00")!} /> <DecimalText value={parseQuantity("1.000")!} /> <DecimalText value={parsePercent("25.00")!} />
      </p>,
    );
    expect(container.textContent).toBe("850.00 1.000 25.00");
  });
});

describe("states the shell relies on", () => {
  it("shows a loading state", () => {
    render(<Loading />);
    expect(screen.getByTestId("loading")).toHaveTextContent("Loading");
  });

  it("shows one generic not-found page that says nothing about why", () => {
    render(<NotFound />);

    expect(screen.getByTestId("not-found")).toHaveTextContent("Not found");
    expect(screen.getByText(/does not exist, or you do not have access/)).toBeInTheDocument();
  });

  it("lets a page error be retried without hiding the shell", async () => {
    const reset = vi.fn();
    render(<PageError error={new Error("boom")} reset={reset} />);

    expect(screen.getByTestId("page-error")).toBeInTheDocument();
    expect(screen.queryByText("boom")).toBeNull(); // internals are not shown
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("lets a shell-level error be retried", async () => {
    const reset = vi.fn();
    render(<RootError error={new Error("secret internals")} reset={reset} />);

    expect(screen.getByTestId("app-error")).toBeInTheDocument();
    expect(screen.queryByText(/secret internals/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
