"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition, type ReactNode } from "react";

import { BUTTON_VARIANTS } from "@/components/ui/Button";
import { apiFetch } from "@/lib/api/client";
import type { Appointment, AppointmentUpdate } from "@/lib/api/types";
import { SNAP_MINUTES, dayDifference, movedTo, resizedTo, shiftedByDays, snapMinutes, timeText } from "@/lib/calendar";

/** Pixels moved before a press becomes a drag (below it the press is a click that opens the booking). */
const DRAG_THRESHOLD = 5;
const MIN_HEIGHT = 14;

interface Press {
  element: HTMLElement;
  kind: "move" | "resize";
  id: string;
  version: number;
  startsAt: string;
  endsAt: string;
  who: string;
  x: number;
  y: number;
  top: number;
  height: number;
  active: boolean;
  /** What would be saved if it were dropped now, or null (outside the grid, or nothing changes). */
  change: AppointmentUpdate | null;
}

/**
 * Makes the bookings inside it draggable. The calendar itself is rendered on the server; this only listens (pointer events, so mouse,
 * pen and finger work the same) for a press on a booking marked `data-movable`, and for its bottom edge `data-resize`.
 *
 *   week/day grid   drop on another time, day or (in the day view) another person's column; drag the bottom edge to change the end;
 *   month grid      drop on another day (the time of day stays).
 *
 * Times snap to quarter hours. A drop is saved with the version the page was drawn from (If-Match): if someone else changed the booking
 * meanwhile it snaps back and says so. The edit form stays the way to change a booking with the keyboard.
 */
export function CalendarDnD({
  orgId,
  zone,
  mode,
  firstHour = 0,
  hourPx = 48,
  enabled,
  children,
}: {
  orgId: string;
  zone: string | null;
  mode: "time" | "month";
  firstHour?: number;
  hourPx?: number;
  enabled: boolean;
  children: ReactNode;
}) {
  const router = useRouter();
  const [refreshing, startTransition] = useTransition();
  const root = useRef<HTMLDivElement>(null);
  const [hint, setHint] = useState<{ x: number; y: number; text: string } | null>(null);
  const [message, setMessage] = useState<{ text: string; problem: boolean } | null>(null);
  /** A finished booking was dropped: the answer to "move it anyway?" decides whether the move is saved or the booking snaps back. */
  const [asking, setAsking] = useState<{ resize: boolean; yes: () => void; no: () => void } | null>(null);
  const settled = useRef<HTMLElement[]>([]);
  const waited = useRef(false);
  const dragged = useRef(false);

  const clear = (element: HTMLElement) => {
    element.style.transform = "";
    element.style.zIndex = "";
    element.style.opacity = "";
    element.style.height = element.dataset.height ?? "";
    delete element.dataset.dragging;
  };

  // A dropped booking stays where it was dropped until the page has been redrawn with its new time, then the leftover offset goes.
  useEffect(() => {
    if (refreshing) waited.current = true;
    else if (waited.current) {
      waited.current = false;
      for (const element of settled.current) clear(element);
      settled.current = [];
    }
  }, [refreshing]);

  useEffect(() => {
    const container = root.current;
    if (!container || !enabled) return;

    const keepClickFromOpening = (event: MouseEvent) => {
      if (!dragged.current) return;
      event.preventDefault();
      event.stopPropagation();
    };

    const targetAt = (event: PointerEvent, selector: string) => document.elementsFromPoint(event.clientX, event.clientY).find((element) => element.matches(selector)) as HTMLElement | undefined;

    function changeFor(press: Press, event: PointerEvent): { change: AppointmentUpdate | null; text: string } {
      if (mode === "month") {
        const cell = targetAt(event, "[data-month-day]");
        const from = press.element.closest<HTMLElement>("[data-month-day]")?.dataset.day;
        if (!cell?.dataset.day || !from) return { change: null, text: "" };
        const days = dayDifference(from, cell.dataset.day);
        return { change: days === 0 ? null : shiftedByDays(press.startsAt, press.endsAt, days, zone), text: `Move to ${cell.dataset.day}` };
      }
      if (press.kind === "resize") {
        const column = press.element.closest<HTMLElement>("[data-column]");
        if (!column?.dataset.day) return { change: null, text: "" };
        const raw = firstHour * 60 + ((event.clientY - column.getBoundingClientRect().top) / hourPx) * 60;
        const minutes = Math.min(24 * 60, Math.max(0, Math.round(raw / SNAP_MINUTES) * SNAP_MINUTES));
        const end = resizedTo(press.startsAt, column.dataset.day, minutes, zone); // the column a booking is in is the day it starts on
        return { change: end && end !== new Date(press.endsAt).toISOString() ? { ends_at: end } : null, text: `Ends ${timeText(minutes % (24 * 60))}` };
      }
      const column = targetAt(event, "[data-column]");
      if (!column?.dataset.day) return { change: null, text: "" };
      // Nobody is not a person to give a booking to (an older booking that has none may still move within its own column).
      if (column.dataset.assign === "true" && (column.dataset.who ?? "") === "" && press.who !== "") return { change: null, text: "Drop it in a person's column" };
      const raw = firstHour * 60 + ((event.clientY - (press.y - press.top) - column.getBoundingClientRect().top) / hourPx) * 60;
      const minutes = snapMinutes(raw);
      const moved = movedTo(press.startsAt, press.endsAt, column.dataset.day, minutes, zone);
      const body: AppointmentUpdate = { ...moved };
      if (column.dataset.assign === "true" && (column.dataset.who ?? "") !== press.who) body.practitioner_user_id = column.dataset.who || null;
      const same = moved.starts_at === new Date(press.startsAt).toISOString() && moved.ends_at === new Date(press.endsAt).toISOString() && body.practitioner_user_id === undefined;
      return { change: same ? null : body, text: `${column.dataset.day} ${timeText(minutes)}${body.practitioner_user_id !== undefined ? " · other person" : ""}` };
    }

    function down(event: PointerEvent) {
      if (event.button !== 0 || event.pointerType === "mouse" && event.buttons !== 1) return;
      const target = event.target as HTMLElement;
      const element = target.closest<HTMLElement>("[data-movable]");
      if (!element || !container?.contains(element)) return;
      const press: Press = {
        element,
        kind: target.closest("[data-resize]") ? "resize" : "move",
        id: element.dataset.appointmentId ?? "",
        version: Number(element.dataset.version),
        startsAt: element.dataset.start ?? "",
        endsAt: element.dataset.end ?? "",
        who: element.dataset.who ?? "",
        x: event.clientX,
        y: event.clientY,
        top: element.getBoundingClientRect().top,
        height: element.getBoundingClientRect().height,
        active: false,
        change: null,
      };
      element.dataset.height = element.style.height;
      element.setPointerCapture(event.pointerId);

      const move = (moveEvent: PointerEvent) => {
        const dx = moveEvent.clientX - press.x;
        const dy = moveEvent.clientY - press.y;
        if (!press.active && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
        press.active = true;
        dragged.current = true;
        element.dataset.dragging = "true";
        element.style.zIndex = "30";
        element.style.opacity = "0.85";
        if (press.kind === "resize") element.style.height = `${Math.max(MIN_HEIGHT, press.height + dy)}px`;
        else element.style.transform = `translate(${dx}px, ${dy}px)`;
        const { change, text } = changeFor(press, moveEvent);
        press.change = change;
        setHint({ x: moveEvent.clientX, y: moveEvent.clientY, text: text || "Drop on the calendar" });
      };

      const end = async (endEvent: PointerEvent) => {
        element.removeEventListener("pointermove", move);
        element.removeEventListener("pointerup", end);
        element.removeEventListener("pointercancel", end);
        if (element.hasPointerCapture(endEvent.pointerId)) element.releasePointerCapture(endEvent.pointerId);
        setHint(null);
        if (!press.active) return; // a click: the booking opens
        setTimeout(() => (dragged.current = false), 0);
        const change = endEvent.type === "pointercancel" ? null : changeFor(press, endEvent).change;
        if (change === null) {
          clear(element);
          return;
        }
        const save = async () => {
          const result = await apiFetch<Appointment>(orgId, `/appointments/${press.id}`, { method: "PATCH", body: change, ifMatch: press.version });
          if (result.ok) {
            settled.current.push(element);
            setMessage({ text: "Saved.", problem: false });
            startTransition(() => router.refresh());
            return;
          }
          clear(element);
          const stale = result.error.kind === "conflict" && result.error.code === "stale_record";
          setMessage({
            text: stale ? "This booking was changed by someone else, so it was not moved. The calendar has been reloaded." : `It was not moved: ${result.error.message}`,
            problem: true,
          });
          startTransition(() => router.refresh());
        };
        // A booking that has ended is not moved by a slip of the hand: the person confirms first (also when the page was drawn before it ended).
        const finished = element.dataset.done === "true" || new Date(press.endsAt).getTime() < Date.now();
        if (finished) {
          setAsking({
            resize: press.kind === "resize",
            yes: () => {
              setAsking(null);
              void save();
            },
            no: () => {
              setAsking(null);
              clear(element);
            },
          });
          return;
        }
        await save();
      };

      element.addEventListener("pointermove", move);
      element.addEventListener("pointerup", end);
      element.addEventListener("pointercancel", end);
    }

    container.addEventListener("pointerdown", down);
    container.addEventListener("click", keepClickFromOpening, true);
    return () => {
      container.removeEventListener("pointerdown", down);
      container.removeEventListener("click", keepClickFromOpening, true);
    };
  }, [enabled, mode, orgId, zone, firstHour, hourPx, router]);

  return (
    <div ref={root} className="flex flex-col gap-2" data-testid="calendar-dnd">
      {children}
      {message && (
        <p role={message.problem ? "alert" : "status"} data-testid="calendar-message" className={`text-sm ${message.problem ? "text-red-700" : "text-secondary"}`}>
          {message.text}
        </p>
      )}
      {asking && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-testid="finished-backdrop">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="finished-title"
            className="panel flex max-w-sm flex-col gap-3 shadow-lg"
            data-testid="finished-dialog"
            onKeyDown={(event) => event.key === "Escape" && asking.no()}
          >
            <h2 id="finished-title" className="text-base font-semibold">
              This appointment is already finished
            </h2>
            <p className="text-sm text-secondary">{asking.resize ? "Do you want to change when it ended?" : "Do you want to move it?"}</p>
            <div className="flex gap-3">
              <button type="button" onClick={asking.yes} className={BUTTON_VARIANTS.primary} data-testid="finished-yes">
                Yes, {asking.resize ? "change it" : "move it"}
              </button>
              <button type="button" autoFocus onClick={asking.no} className={BUTTON_VARIANTS.secondary} data-testid="finished-no">
                No, leave it
              </button>
            </div>
          </div>
        </div>
      )}
      {hint && (
        <div aria-hidden className="pointer-events-none fixed z-50 rounded bg-zinc-900 px-2 py-1 text-xs text-white shadow" style={{ left: hint.x + 14, top: hint.y + 14 }} data-testid="drag-hint">
          {hint.text}
        </div>
      )}
    </div>
  );
}
