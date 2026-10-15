import type { JournalMark, MarkSide, MarkTool } from "@/lib/api/types";

/** How many points each tool has (the backend refuses any other number). */
export const POINTS_OF: Record<MarkTool, number> = { line: 2, arrow: 2, x: 1, o: 1 };
export const TOOL_LABELS: Record<MarkTool, string> = { line: "Line", arrow: "Arrow", x: "X", o: "O" };
export const SIDE_LABELS: Record<MarkSide, string> = { left: "Left side", right: "Right side" };
export const MAX_MARKS = 200;
const HISTORY_LIMIT = 100;
/** A drag shorter than this (a fraction of the diagram) is a click, not a line. */
export const MIN_LINE_LENGTH = 0.02;

export interface EditorState {
  marks: JournalMark[];
  past: JournalMark[][];
  future: JournalMark[][];
}

export type EditorAction =
  | { type: "add"; mark: JournalMark }
  | { type: "replace"; mark: JournalMark }
  | { type: "remove"; id: string }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "load"; marks: JournalMark[] };

/** The number the next mark gets: after the highest in use (a number is never given to a different mark while the entry is open). */
export const nextNumber = (marks: JournalMark[]): number => Math.max(0, ...marks.map((mark) => mark.number ?? 0)) + 1;

/** The number to show for a mark: its own, or (for a mark from before numbers existed) its place in the entry. */
export const numberOf = (mark: JournalMark, marks: JournalMark[]): number => mark.number ?? marks.findIndex((other) => other.id === mark.id) + 1;

export const initialState = (marks: JournalMark[] = []): EditorState => ({ marks, past: [], future: [] });

function commit(state: EditorState, marks: JournalMark[]): EditorState {
  return { marks, past: [...state.past, state.marks].slice(-HISTORY_LIMIT), future: [] };
}

/** Marks change only through this reducer, so every step can be undone and redone. */
export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "add":
      return state.marks.length >= MAX_MARKS ? state : commit(state, [...state.marks, { ...action.mark, number: action.mark.number ?? nextNumber(state.marks) }]);
    case "replace":
      return commit(
        state,
        state.marks.map((mark) => (mark.id === action.mark.id ? action.mark : mark)),
      );
    case "remove":
      return state.marks.some((mark) => mark.id === action.id) ? commit(state, state.marks.filter((mark) => mark.id !== action.id)) : state;
    case "undo": {
      const previous = state.past[state.past.length - 1];
      return previous === undefined ? state : { marks: previous, past: state.past.slice(0, -1), future: [state.marks, ...state.future] };
    }
    case "redo": {
      const next = state.future[0];
      return next === undefined ? state : { marks: next, past: [...state.past, state.marks], future: state.future.slice(1) };
    }
    case "load":
      return initialState(action.marks);
  }
}

/** Whether two lists hold the same marks. Compared mark by mark, NOT as JSON text: the backend writes a mark's fields in its own order, and the
 * same marks in another key order must not make a saved entry look changed. */
export function sameMarks(a: JournalMark[], b: JournalMark[]): boolean {
  return (
    a.length === b.length &&
    a.every((mark, index) => {
      const other = b[index];
      return (
        mark.id === other.id &&
        (mark.number ?? null) === (other.number ?? null) &&
        mark.side === other.side &&
        mark.tool === other.tool &&
        mark.points.length === other.points.length &&
        mark.points.every((point, at) => point[0] === other.points[at][0] && point[1] === other.points[at][1])
      );
    })
  );
}

export const clamp01 = (value: number): number => Math.min(1, Math.max(0, value));

/** Move every point of a mark by (dx, dy), stopping at the edges of the diagram without changing the mark's shape. */
export function moved(mark: JournalMark, dx: number, dy: number): JournalMark {
  const xs = mark.points.map(([x]) => x);
  const ys = mark.points.map(([, y]) => y);
  const fitX = Math.min(Math.max(dx, -Math.min(...xs)), 1 - Math.max(...xs));
  const fitY = Math.min(Math.max(dy, -Math.min(...ys)), 1 - Math.max(...ys));
  return { ...mark, points: mark.points.map(([x, y]) => [x + fitX, y + fitY]) };
}

export function newMark(tool: MarkTool, side: MarkSide, points: number[][]): JournalMark {
  return { id: globalThis.crypto.randomUUID(), side, tool, points: points.map(([x, y]) => [clamp01(x), clamp01(y)]) };
}

export const length = (mark: JournalMark): number => (mark.points.length < 2 ? 0 : Math.hypot(mark.points[1][0] - mark.points[0][0], mark.points[1][1] - mark.points[0][1]));

/** The text alternative of a mark, for screen readers and the marks list: what it is and where on the diagram. */
export function describe(mark: JournalMark): string {
  const spot = ([x, y]: number[]) => `${Math.round(x * 100)} % across, ${Math.round(y * 100)} % down`;
  const where = mark.points.length === 1 ? `at ${spot(mark.points[0])}` : `from ${spot(mark.points[0])} to ${spot(mark.points[1])}`;
  return `${TOOL_LABELS[mark.tool]} on the horse's ${mark.side} side, ${where}`;
}
