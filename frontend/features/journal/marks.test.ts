import { describe as suite, expect, it } from "vitest";

import type { JournalMark } from "@/lib/api/types";
import { describe, editorReducer, initialState, length, MAX_MARKS, moved, newMark, nextNumber, numberOf, POINTS_OF, sameMarks } from "./marks";

const mark = (id: string, overrides: Partial<JournalMark> = {}): JournalMark => ({ id, side: "left", tool: "x", points: [[0.5, 0.5]], ...overrides });

suite("the mark editor's history", () => {
  it("adds, replaces and removes marks, and every step can be undone and redone", () => {
    let state = initialState();
    state = editorReducer(state, { type: "add", mark: mark("a") });
    state = editorReducer(state, { type: "add", mark: mark("b") });
    state = editorReducer(state, { type: "replace", mark: mark("a", { points: [[0.1, 0.1]], number: 1 }) });
    state = editorReducer(state, { type: "remove", id: "b" });
    expect(state.marks).toEqual([mark("a", { points: [[0.1, 0.1]], number: 1 })]);

    state = editorReducer(state, { type: "undo" });
    expect(state.marks.map((m) => m.id)).toEqual(["a", "b"]);
    state = editorReducer(state, { type: "undo" });
    expect(state.marks).toEqual([mark("a", { number: 1 }), mark("b", { number: 2 })]);
    state = editorReducer(state, { type: "redo" });
    expect(state.marks[0].points).toEqual([[0.1, 0.1]]);
  });

  it("a new step clears what could be redone, and undo or redo with nothing to do changes nothing", () => {
    let state = editorReducer(initialState(), { type: "add", mark: mark("a") });
    state = editorReducer(state, { type: "undo" });
    expect(state.future).toHaveLength(1);
    state = editorReducer(state, { type: "add", mark: mark("b") });
    expect(state.future).toEqual([]);
    const same = initialState([mark("z")]);
    expect(editorReducer(same, { type: "undo" })).toBe(same);
    expect(editorReducer(same, { type: "redo" })).toBe(same);
    expect(editorReducer(same, { type: "remove", id: "missing" })).toBe(same);
  });

  it("refuses a mark beyond the limit the backend would refuse, and loading starts a fresh history", () => {
    const full = initialState(Array.from({ length: MAX_MARKS }, (_, index) => mark(`m${index}`)));
    expect(editorReducer(full, { type: "add", mark: mark("one-too-many") })).toBe(full);
    const loaded = editorReducer(editorReducer(initialState(), { type: "add", mark: mark("a") }), { type: "load", marks: [mark("b")] });
    expect(loaded).toEqual({ marks: [mark("b")], past: [], future: [] });
  });
});

suite("mark numbers (so the notes can say 'mark 3')", () => {
  it("gives each new mark the next number and a number stays with its mark when another is deleted", () => {
    let state = initialState();
    for (const id of ["a", "b", "c"]) state = editorReducer(state, { type: "add", mark: mark(id) });
    expect(state.marks.map((m) => m.number)).toEqual([1, 2, 3]);
    state = editorReducer(state, { type: "remove", id: "b" });
    expect(state.marks.map((m) => [m.id, m.number])).toEqual([["a", 1], ["c", 3]]); // c is still mark 3
    state = editorReducer(state, { type: "add", mark: mark("d") });
    expect(state.marks.map((m) => m.number)).toEqual([1, 3, 4]); // after the highest, never reusing 2 or 3
    state = editorReducer(state, { type: "replace", mark: { ...state.marks[1], points: [[0.9, 0.9]] } });
    expect(state.marks[1].number).toBe(3); // moving a mark keeps its number
  });

  it("keeps a number it was given and falls back to the place in the entry for a mark from before numbers", () => {
    const state = editorReducer(initialState([mark("old")]), { type: "add", mark: mark("given", { number: 9 }) });
    expect(state.marks[1].number).toBe(9);
    expect(nextNumber(state.marks)).toBe(10);
    expect(numberOf(state.marks[0], state.marks)).toBe(1); // "old" has none: its place
    expect(nextNumber([])).toBe(1);
  });
});

suite("comparing marks", () => {
  it("treats the same marks in another key order as the same, and any real difference as a change", () => {
    const written = mark("a", { number: 1, tool: "arrow", points: [[0.1, 0.2], [0.3, 0.4]] });
    const reordered = { points: [[0.1, 0.2], [0.3, 0.4]], tool: "arrow", side: "left", number: 1, id: "a" } as JournalMark; // the backend's order
    expect(sameMarks([written], [reordered])).toBe(true);
    expect(sameMarks([written], [{ ...written, number: 2 }])).toBe(false);
    expect(sameMarks([written], [{ ...written, points: [[0.1, 0.2], [0.3, 0.41]] }])).toBe(false);
    expect(sameMarks([written], [])).toBe(false);
    expect(sameMarks([], [])).toBe(true);
  });
});

suite("mark geometry", () => {
  it("moves a mark without changing its shape and stops at the edges of the diagram", () => {
    const arrow = mark("a", { tool: "arrow", points: [[0.2, 0.2], [0.4, 0.3]] });
    const shifted = moved(arrow, 0.1, 0.1).points;
    expect(shifted[0][0]).toBeCloseTo(0.3);
    expect(shifted[1][1]).toBeCloseTo(0.4);
    const stuck = moved(arrow, 5, -5);
    expect(stuck.points[0][0]).toBeCloseTo(0.8); // the right end reached 1
    expect(stuck.points[1][0]).toBeCloseTo(1);
    expect(stuck.points[0][1]).toBeCloseTo(0);
    expect(length(stuck)).toBeCloseTo(length(arrow));
  });

  it("makes marks inside the diagram with the right number of points and describes them in words", () => {
    const outside = newMark("x", "right", [[1.4, -0.2]]);
    expect(outside.points).toEqual([[1, 0]]);
    expect(outside.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(POINTS_OF).toEqual({ line: 2, arrow: 2, x: 1, o: 1 });
    expect(describe(mark("a", { side: "right", tool: "o", points: [[0.25, 0.75]] }))).toBe("O on the horse's right side, at 25 % across, 75 % down");
    expect(describe(mark("a", { tool: "arrow", points: [[0, 0], [1, 1]] }))).toBe("Arrow on the horse's left side, from 0 % across, 0 % down to 100 % across, 100 % down");
  });
});
