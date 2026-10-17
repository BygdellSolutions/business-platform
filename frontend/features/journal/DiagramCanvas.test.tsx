import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JournalMark } from "@/lib/api/types";
import { DiagramCanvas, type ActiveTool } from "./DiagramCanvas";

// The diagram occupies a 400 x 300 box at the top left of the page, so a pointer at (200, 150) is at (0.5, 0.5).
beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300, x: 0, y: 0, toJSON: () => ({}) });
});
afterEach(() => vi.restoreAllMocks());

const surface = () => screen.getByTestId("diagram-left");
const pointer = (type: "pointerDown" | "pointerMove" | "pointerUp", x: number, y: number, target: Element = surface()) =>
  fireEvent[type](target, { clientX: x, clientY: y, pointerId: 1 });

function setup(tool: ActiveTool, marks: JournalMark[] = []) {
  const onAdd = vi.fn();
  const onMove = vi.fn();
  const onSelect = vi.fn();
  render(<DiagramCanvas side="left" marks={marks} editable tool={tool} onAdd={onAdd} onMove={onMove} onSelect={onSelect} />);
  return { onAdd, onMove, onSelect };
}

describe("an illustration this build does not have", () => {
  it("is not replaced by another picture: it says so, draws nothing and takes no marks", () => {
    const onAdd = vi.fn();
    render(<DiagramCanvas side="left" marks={[{ id: "m", number: 1, side: "left", tool: "x", points: [[0.5, 0.5]] }]} version={99} editable tool="x" onAdd={onAdd} />);
    expect(screen.getByTestId("unsupported-diagram").textContent).toContain("diagram version 99");
    expect(screen.queryByTestId("diagram-left")).toBeNull(); // no picture, so no mark can land on the wrong place
    expect(screen.queryByTestId("mark")).toBeNull();
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("still draws the versions it has", () => {
    render(<DiagramCanvas side="left" marks={[]} version={1} />);
    expect(screen.getByTestId("diagram-left")).toBeTruthy();
    expect(screen.queryByTestId("unsupported-diagram")).toBeNull();
  });
});

describe("drawing on the diagram", () => {
  it("places an X or an O where tapped, as fractions of the diagram", () => {
    const { onAdd, onSelect } = setup("x");
    pointer("pointerDown", 100, 75);
    expect(onAdd).toHaveBeenCalledTimes(1);
    const mark = onAdd.mock.calls[0][0] as JournalMark;
    expect(mark).toMatchObject({ side: "left", tool: "x", points: [[0.25, 0.25]] });
    expect(onSelect).toHaveBeenCalledWith(mark.id);
  });

  it("draws an arrow by dragging from one point to another", () => {
    const { onAdd } = setup("arrow");
    pointer("pointerDown", 40, 30);
    pointer("pointerMove", 200, 150);
    pointer("pointerUp", 360, 270);
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd.mock.calls[0][0]).toMatchObject({ tool: "arrow", points: [[0.1, 0.1], [0.9, 0.9]] });
  });

  it("treats a tiny drag as a tap and adds no line", () => {
    const { onAdd } = setup("line");
    pointer("pointerDown", 100, 100);
    pointer("pointerUp", 101, 100);
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("keeps a mark inside the diagram whatever the pointer does", () => {
    const { onAdd } = setup("line");
    pointer("pointerDown", -50, -50);
    pointer("pointerUp", 900, 900);
    expect(onAdd.mock.calls[0][0].points).toEqual([[0, 0], [1, 1]]);
  });

  it("draws nothing at all when it is read-only", () => {
    const onAdd = vi.fn();
    render(<DiagramCanvas side="left" marks={[]} tool="x" onAdd={onAdd} />);
    pointer("pointerDown", 100, 100);
    expect(onAdd).not.toHaveBeenCalled();
  });
});

describe("selecting and moving", () => {
  const arrow: JournalMark = { id: "11111111-1111-4111-8111-111111111111", side: "left", tool: "arrow", points: [[0.2, 0.2], [0.4, 0.4]] };

  it("selects a mark and drags it, saving the move once when the pointer is released", () => {
    const { onMove, onSelect } = setup("select", [arrow]);
    const shape = screen.getByTestId("mark");
    pointer("pointerDown", 80, 60, shape);
    expect(onSelect).toHaveBeenCalledWith(arrow.id);
    pointer("pointerMove", 120, 90);
    expect(onMove).not.toHaveBeenCalled(); // still dragging
    pointer("pointerUp", 120, 90);
    expect(onMove).toHaveBeenCalledTimes(1);
    const final = onMove.mock.calls[0][0] as JournalMark;
    expect(final.id).toBe(arrow.id);
    expect(final.points[0][0]).toBeCloseTo(0.3);
    expect(final.points[1][1]).toBeCloseTo(0.5);
  });

  it("deselects when the empty diagram is tapped", () => {
    const { onSelect } = setup("select", [arrow]);
    pointer("pointerDown", 300, 250);
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("keeps an old entry on its own illustration, with its marks in the same place on the picture", () => {
    const circle: JournalMark = { id: "33333333-3333-4333-8333-333333333333", side: "left", tool: "o", points: [[0.25, 0.5]] };
    const { unmount } = render(<DiagramCanvas side="left" marks={[circle]} version={1} />);
    expect(screen.getByTestId("diagram-left").getAttribute("data-diagram-version")).toBe("1");
    expect(screen.getByTestId("diagram-left").getAttribute("viewBox")).toBe("0 0 400 300");
    expect(document.querySelector("image")).toBeNull(); // the line drawing, not the engraving
    const drawn = () => [...document.querySelectorAll("circle")].find((c) => c.getAttribute("fill") === "none");
    expect(drawn()?.getAttribute("cx")).toBe("100"); // 25 % of 400
    unmount();

    render(<DiagramCanvas side="left" marks={[circle]} version={2} />);
    expect(screen.getByTestId("diagram-left").getAttribute("viewBox")).toBe("0 0 1000 711");
    expect(document.querySelector("image")?.getAttribute("href")).toBe("/diagrams/horse-v2.svg");
    expect(drawn()?.getAttribute("cx")).toBe("250"); // the same 25 % of the width, on the other picture
  });

  it("writes each mark's number in a badge on the picture, the same number as in the list and the notes", () => {
    const marks: JournalMark[] = [
      { id: "11111111-1111-4111-8111-111111111111", number: 3, side: "left", tool: "x", points: [[0.5, 0.5]] },
      { id: "22222222-2222-4222-8222-222222222222", number: 7, side: "left", tool: "arrow", points: [[0.2, 0.2], [0.4, 0.4]] },
      { id: "33333333-3333-4333-8333-333333333333", side: "left", tool: "o", points: [[0.7, 0.7]] }, // from before numbers: its place, 3
    ];
    render(<DiagramCanvas side="left" marks={marks} />);
    expect(screen.getAllByTestId("mark-number").map((badge) => badge.textContent)).toEqual(["3", "7", "3"]);
  });

  it("mirrors the picture for the right side, so the head is on the right", () => {
    render(<DiagramCanvas side="right" marks={[]} />);
    expect(document.querySelector("image")?.parentElement?.getAttribute("transform")).toBe("translate(1000 0) scale(-1 1)");
  });

  it("shows only the marks of its own side, and says how many in words", () => {
    const right: JournalMark = { ...arrow, id: "22222222-2222-4222-8222-222222222222", side: "right" };
    render(<DiagramCanvas side="left" marks={[arrow, right]} />);
    expect(screen.getAllByTestId("mark")).toHaveLength(1);
    expect(screen.getByRole("img", { name: "The horse's left side, 1 mark" })).toBeTruthy();
  });
});
