import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Modal } from "@/components/ui/Modal";

describe("Modal", () => {
  it("is shown when open, with its title, and not shown when closed", () => {
    const { rerender } = render(
      <Modal open={false} onClose={() => {}} title="Record payment">
        <p>inside</p>
      </Modal>,
    );
    expect(screen.queryByRole("dialog")).toBeNull(); // closed: not in the accessibility tree
    rerender(
      <Modal open onClose={() => {}} title="Record payment">
        <p>inside</p>
      </Modal>,
    );
    expect(screen.getByRole("dialog", { name: "Record payment" })).toBeInTheDocument();
    expect(screen.getByText("inside")).toBeVisible();
  });

  it("keeps what is inside mounted while closed, so nothing typed is lost", () => {
    const { rerender } = render(
      <Modal open onClose={() => {}} title="T">
        <input aria-label="note" defaultValue="" />
      </Modal>,
    );
    fireEvent.change(screen.getByLabelText("note", { selector: "input" }), { target: { value: "typed" } });
    rerender(
      <Modal open={false} onClose={() => {}} title="T">
        <input aria-label="note" defaultValue="" />
      </Modal>,
    );
    expect(screen.getByLabelText("note", { selector: "input" })).toHaveValue("typed");
  });

  it("closes with the ×, with Escape (the dialog's close event) and with a press on the backdrop, but not on a press inside", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="T" testId="d">
        <button type="button">inside</button>
      </Modal>,
    );
    fireEvent.click(screen.getByText("inside"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("modal-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("d")); // the backdrop is the dialog element itself
    expect(onClose).toHaveBeenCalledTimes(2);
    fireEvent(screen.getByTestId("d"), new Event("close"));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("does not report a close the page itself asked for, so opening another window in its place does not close that one", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Modal open onClose={onClose} title="T" testId="d">
        <p>x</p>
      </Modal>,
    );
    rerender(
      <Modal open={false} onClose={onClose} title="T" testId="d">
        <p>x</p>
      </Modal>,
    );
    fireEvent(screen.getByTestId("d"), new Event("close")); // the browser says the dialog closed, after the page closed it
    expect(onClose).not.toHaveBeenCalled();
  });
});
