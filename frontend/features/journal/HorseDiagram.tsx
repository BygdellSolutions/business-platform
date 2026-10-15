import type { ComponentType } from "react";

import type { MarkSide } from "@/lib/api/types";

/**
 * The bundled horse illustrations. An entry stores which one its marks were drawn on (`diagram_version`), and marks are fractions
 * of that picture, so a new illustration can be added without moving a single old mark: old entries keep showing their own.
 * Every illustration shows the horse's LEFT side with the head to the viewer's left; the right side is the same picture mirrored.
 */
export interface Illustration {
  /** The drawing box in SVG units (its proportions are the picture's; only the ratio matters). */
  width: number;
  height: number;
  Art: ComponentType<{ side: MarkSide }>;
}

const mirrored = (side: MarkSide, width: number) => (side === "right" ? `translate(${width} 0) scale(-1 1)` : undefined);

// --- version 1: a simple line drawing (kept so entries made on it still show their marks on it) ------------------------------------

const BODY =
  "M110 40 C140 55 165 85 182 98 C215 92 270 92 300 100 C320 105 324 140 312 168 L292 172 C265 178 215 178 185 172 L158 160 C150 135 135 112 105 96 C95 100 82 108 70 112 C60 110 58 100 64 90 C75 75 90 55 110 40 Z";
const LEGS = [
  "M166 166 L184 170 L182 238 L186 262 L166 262 L169 238 Z",
  "M142 160 L158 162 L156 236 L160 262 L142 262 L145 236 Z",
  "M286 168 L306 164 L304 232 L308 262 L288 262 L290 232 Z",
  "M262 172 L284 172 L282 234 L286 262 L266 262 L268 234 Z",
];
const TAIL = "M308 104 C336 122 342 162 332 206 C324 172 318 142 304 120 Z";
const EAR = "M106 40 L112 18 L122 38 Z";
const MANE = "M112 40 C138 56 162 84 180 98";

function HorseOutline({ side }: { side: MarkSide }) {
  return (
    <g transform={mirrored(side, 400)} fill="#f4efe6" stroke="#5b4a3a" strokeWidth={2} strokeLinejoin="round" aria-hidden="true">
      {LEGS.slice().reverse().map((leg) => (
        <path key={leg} d={leg} />
      ))}
      <path d={TAIL} />
      <path d={BODY} />
      <path d={EAR} />
      <path d={MANE} fill="none" strokeWidth={4} strokeLinecap="round" />
      <circle cx={86} cy={72} r={3} fill="#5b4a3a" stroke="none" />
    </g>
  );
}

// --- version 2: an engraved, lifelike standing horse (public/diagrams/horse-v2.svg; its source and licence are in that file) -----

const V2 = { width: 1000, height: 711 }; // the proportions of the picture

function HorseEngraving({ side }: { side: MarkSide }) {
  return (
    <g transform={mirrored(side, V2.width)} aria-hidden="true">
      <image href="/diagrams/horse-v2.svg" x={0} y={0} width={V2.width} height={V2.height} preserveAspectRatio="none" />
    </g>
  );
}

export const ILLUSTRATIONS: Record<number, Illustration> = {
  1: { width: 400, height: 300, Art: HorseOutline },
  2: { ...V2, Art: HorseEngraving },
};

/** The illustration new entries are drawn on (the backend's DIAGRAM_VERSION). */
export const LATEST_DIAGRAM = 2;

/** The illustration of a stored version; one this build does not know is shown on the newest picture rather than not at all. */
export function illustrationOf(version: number): Illustration {
  return ILLUSTRATIONS[version] ?? ILLUSTRATIONS[LATEST_DIAGRAM];
}
