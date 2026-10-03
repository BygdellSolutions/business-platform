import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { ListFilters } from "@/components/ui/ListFilters";
import { parseListParams } from "@/lib/list-params";

const props = { label: "Delete", question: "Delete this?", confirmLabel: "Yes, delete", testId: "go" };

describe("ConfirmButton", () => {
  it("the first click only asks; nothing happens until the second step", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmButton {...props} onConfirm={onConfirm} />);

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "Delete this?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument(); // asks again next time
  });

  it("Keep goes back without doing anything", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmButton {...props} onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("is reachable and operable with the keyboard", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmButton {...props} onConfirm={onConfirm} />);
    await userEvent.tab();
    await userEvent.keyboard("{Enter}");
    await userEvent.tab();
    await userEvent.keyboard("{Enter}");
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("when disabled it cannot be opened, and an open question disappears", async () => {
    const onConfirm = vi.fn();
    const { rerender } = render(<ConfirmButton {...props} onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    rerender(<ConfirmButton {...props} onConfirm={onConfirm} disabled />);

    expect(screen.queryByRole("group")).toBeNull();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });
});

describe("ListFilters switches", () => {
  const params = parseListParams({});

  it("shows search and the Active/Inactive status by default", () => {
    render(<ListFilters action="/x" params={params} />);
    expect(screen.getByLabelText("Search")).toBeInTheDocument();
    expect(screen.getByLabelText("Status")).toBeInTheDocument();
  });

  it("can leave out either, for lists whose backend has neither", () => {
    render(<ListFilters action="/x" params={params} search={false} activeStatus={false} />);
    expect(screen.queryByLabelText("Search")).toBeNull();
    expect(screen.queryByLabelText("Status")).toBeNull();
    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear" })).toHaveAttribute("href", "/x");
  });
});
