"use client";

import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import type { JournalMark, MarkSide, MarkTool } from "@/lib/api/types";
import { illustrationOf, LATEST_DIAGRAM, type Illustration } from "./HorseDiagram";
import { clamp01, length, MIN_LINE_LENGTH, moved, newMark, numberOf, SIDE_LABELS } from "./marks";

export type ActiveTool = "select" | MarkTool;

const MARK_COLOR = "#c0392b";
const SELECTED_COLOR = "#1d4ed8";

/** Marks are drawn in the picture's own units; one "unit" is a 400th of its width, so a mark looks the same on every illustration. */
function geometry(width: number, height: number) {
  const k = width / 400;
  const px = (x: number) => x * width;
  const py = (y: number) => y * height;
  /** The arrow head: two short strokes back from the end of the line. */
  const arrowHead = ([x1, y1]: number[], [x2, y2]: number[]) => {
    const angle = Math.atan2(py(y2) - py(y1), px(x2) - px(x1));
    const spread = Math.PI / 7;
    const wing = (turn: number) => `${px(x2) - 14 * k * Math.cos(angle + turn)},${py(y2) - 14 * k * Math.sin(angle + turn)}`;
    return `${wing(spread)} ${px(x2)},${py(y2)} ${wing(-spread)}`;
  };
  return { k, px, py, arrowHead };
}

function MarkShape({ mark, selected, width, height }: { mark: JournalMark; selected: boolean; width: number; height: number }) {
  const { k, px, py, arrowHead } = geometry(width, height);
  const color = selected ? SELECTED_COLOR : MARK_COLOR;
  const common = { stroke: color, strokeWidth: (selected ? 4 : 3) * k, strokeLinecap: "round" as const, fill: "none" };
  const size = 9 * k; // half the size of an X, the radius of an O
  const [a, b] = mark.points;
  switch (mark.tool) {
    case "line":
      return <line x1={px(a[0])} y1={py(a[1])} x2={px(b[0])} y2={py(b[1])} {...common} />;
    case "arrow":
      return (
        <g {...common}>
          <line x1={px(a[0])} y1={py(a[1])} x2={px(b[0])} y2={py(b[1])} />
          <polyline points={arrowHead(a, b)} strokeLinejoin="round" />
        </g>
      );
    case "x":
      return (
        <g {...common}>
          <line x1={px(a[0]) - size} y1={py(a[1]) - size} x2={px(a[0]) + size} y2={py(a[1]) + size} />
          <line x1={px(a[0]) - size} y1={py(a[1]) + size} x2={px(a[0]) + size} y2={py(a[1]) - size} />
        </g>
      );
    case "o":
      return <circle cx={px(a[0])} cy={py(a[1])} r={size} {...common} />;
  }
}

/** Where a mark's number badge sits: up and to the right of a symbol, or just beyond the tail of a line or arrow. */
function badgeAt(mark: JournalMark, width: number, height: number): [number, number] {
  const { k, px, py } = geometry(width, height);
  const [a, b] = mark.points;
  if (!b) return [px(a[0]) + 15 * k, py(a[1]) - 15 * k];
  const dx = px(a[0]) - px(b[0]);
  const dy = py(a[1]) - py(b[1]);
  const away = Math.hypot(dx, dy) || 1;
  return [px(a[0]) + (dx / away) * 14 * k, py(a[1]) + (dy / away) * 14 * k];
}

/** The mark's number in a small round badge, the same number the list under the picture and the text use. */
function NumberBadge({ mark, number, selected, width, height }: { mark: JournalMark; number: number; selected: boolean; width: number; height: number }) {
  const k = width / 400;
  const [x, y] = badgeAt(mark, width, height);
  const color = selected ? SELECTED_COLOR : MARK_COLOR;
  return (
    <g aria-hidden="true" data-testid="mark-number">
      <circle cx={x} cy={y} r={9 * k} fill="#ffffff" stroke={color} strokeWidth={1.5 * k} />
      <text x={x} y={y} fontSize={11 * k} fontWeight={700} fill={color} textAnchor="middle" dominantBaseline="central">
        {number}
      </text>
    </g>
  );
}

/**
 * One side of the horse with its marks. Read-only (`editable` false), or editable: the line and arrow tools drag from one point to
 * another, X and O place a symbol where tapped, and "select" picks a mark and drags it. Everything is in fractions of the
 * picture, taken from the SVG's own size when the pointer moves, so it works the same on a phone, a tablet and a desktop screen.
 * (`touch-action: none` keeps a finger drawing instead of scrolling the page.) `version` is the illustration the marks were
 * drawn on; an entry always shows its own.
 */
interface CanvasProps {
  side: MarkSide;
  marks: JournalMark[];
  version?: number;
  editable?: boolean;
  tool?: ActiveTool;
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
  onAdd?: (mark: JournalMark) => void;
  onMove?: (mark: JournalMark) => void;
}

/** An entry drawn on an illustration this build does not have is not drawn on another one: it says so (the marks are still listed in words). */
export function DiagramCanvas(props: CanvasProps) {
  const version = props.version ?? LATEST_DIAGRAM;
  const illustration = illustrationOf(version);
  if (illustration === null) {
    return (
      <div role="alert" data-testid="unsupported-diagram" className="rounded border border-amber-400 p-3 text-sm">
        This entry was drawn on diagram version {version}, which this version of the application does not have, so its marks are not shown on a picture
        (they would land on the wrong place). The marks are listed below. Reload the page; if this stays, the application needs to be updated.
      </div>
    );
  }
  return <SupportedCanvas {...props} version={version} illustration={illustration} />;
}

function SupportedCanvas({
  side,
  marks,
  version,
  editable = false,
  tool = "select",
  selectedId = null,
  onSelect,
  onAdd,
  onMove,
  illustration,
}: CanvasProps & { version: number; illustration: Illustration }) {
  const { width, height, Art } = illustration;
  const { k } = geometry(width, height);
  const svg = useRef<SVGSVGElement>(null);
  const [drawing, setDrawing] = useState<{ start: number[]; current: number[] } | null>(null);
  const [dragging, setDragging] = useState<{ mark: JournalMark; from: number[]; current: JournalMark } | null>(null);
  const here = marks.filter((mark) => mark.side === side);

  function point(event: ReactPointerEvent): number[] {
    const box = svg.current?.getBoundingClientRect();
    if (!box || box.width === 0 || box.height === 0) return [0, 0];
    return [clamp01((event.clientX - box.left) / box.width), clamp01((event.clientY - box.top) / box.height)];
  }

  function onBackgroundDown(event: ReactPointerEvent) {
    if (!editable) return;
    const at = point(event);
    if (tool === "select") {
      onSelect?.(null);
      return;
    }
    if (tool === "x" || tool === "o") {
      const mark = newMark(tool, side, [at]);
      onAdd?.(mark);
      onSelect?.(mark.id);
      return;
    }
    svg.current?.setPointerCapture?.(event.pointerId);
    setDrawing({ start: at, current: at });
  }

  function onMarkDown(event: ReactPointerEvent, mark: JournalMark) {
    if (!editable || tool !== "select") return;
    event.stopPropagation();
    onSelect?.(mark.id);
    svg.current?.setPointerCapture?.(event.pointerId);
    setDragging({ mark, from: point(event), current: mark });
  }

  function onMovePointer(event: ReactPointerEvent) {
    if (drawing) setDrawing({ ...drawing, current: point(event) });
    if (dragging) {
      const at = point(event);
      setDragging({ ...dragging, current: moved(dragging.mark, at[0] - dragging.from[0], at[1] - dragging.from[1]) });
    }
  }

  function onUp(event: ReactPointerEvent) {
    if (drawing && (tool === "line" || tool === "arrow")) {
      const mark = newMark(tool, side, [drawing.start, point(event)]);
      setDrawing(null);
      if (length(mark) >= MIN_LINE_LENGTH) {
        onAdd?.(mark);
        onSelect?.(mark.id);
      }
    }
    if (dragging) {
      const final = dragging.current;
      setDragging(null);
      if (JSON.stringify(final.points) !== JSON.stringify(dragging.mark.points)) onMove?.(final);
    }
  }

  const shown = here.map((mark) => (dragging && dragging.mark.id === mark.id ? dragging.current : mark));
  const preview = drawing && (tool === "line" || tool === "arrow") ? newMark(tool, side, [drawing.start, drawing.current]) : null;

  return (
    <svg
      ref={svg}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`The horse's ${side} side, ${here.length === 0 ? "no marks" : `${here.length} ${here.length === 1 ? "mark" : "marks"}`}`}
      data-testid={`diagram-${side}`}
      data-diagram-version={version}
      className="w-full max-w-3xl rounded border border-zinc-300 bg-white dark:border-zinc-700"
      style={{ touchAction: editable ? "none" : "auto", cursor: editable && tool !== "select" ? "crosshair" : "default" }}
      onPointerDown={onBackgroundDown}
      onPointerMove={onMovePointer}
      onPointerUp={onUp}
      onPointerCancel={() => {
        setDrawing(null);
        setDragging(null);
      }}
    >
      <title>{SIDE_LABELS[side]}</title>
      <rect width={width} height={height} fill="#ffffff" data-testid={`diagram-${side}-surface`} />
      <Art side={side} />
      <text x={8 * k} y={18 * k} fontSize={12 * k} fill="#5b4a3a" aria-hidden="true">
        {side === "left" ? "LEFT SIDE of the horse" : "RIGHT SIDE of the horse"}
      </text>
      {shown.map((mark) => (
        <g key={mark.id} data-testid="mark" data-mark-id={mark.id} data-tool={mark.tool} onPointerDown={(event) => onMarkDown(event, mark)} style={{ cursor: editable && tool === "select" ? "move" : undefined }}>
          {/* a wide invisible copy to grab: the visible stroke is too thin for a finger */}
          <g opacity={0} stroke="transparent" strokeWidth={28 * k} aria-hidden="true">
            <MarkHit mark={mark} width={width} height={height} />
          </g>
          <MarkShape mark={mark} selected={mark.id === selectedId} width={width} height={height} />
          <NumberBadge mark={mark} number={numberOf(mark, marks)} selected={mark.id === selectedId} width={width} height={height} />
        </g>
      ))}
      {preview && <MarkShape mark={preview} selected width={width} height={height} />}
    </svg>
  );
}

/** The grabbing area of a mark: a fat line along it, or a disc at a symbol. */
function MarkHit({ mark, width, height }: { mark: JournalMark; width: number; height: number }) {
  const { k, px, py } = geometry(width, height);
  const [a, b] = mark.points;
  if (b) return <line x1={px(a[0])} y1={py(a[1])} x2={px(b[0])} y2={py(b[1])} stroke="#000" strokeWidth={28 * k} strokeLinecap="round" />;
  return <circle cx={px(a[0])} cy={py(a[1])} r={16 * k} fill="#000" />;
}
