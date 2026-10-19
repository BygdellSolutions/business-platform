"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * The time grid covers the whole day, so it scrolls: it opens at `offset` pixels from the top (the morning, or the day's first booking if
 * that is earlier) and keeps the position the person scrolled to while they drag, save and navigate.
 */
export function TimeGridScroller({ offset, children }: { offset: number; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.scrollTo({ top: offset });
  }, [offset]);
  return (
    <div ref={box} className="max-h-[36rem] overflow-auto rounded-lg border border-border-strong" data-testid="time-scroller">
      {children}
    </div>
  );
}
