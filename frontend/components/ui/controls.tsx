import type { InputHTMLAttributes } from "react";

/** A filter's input or select. Colours, radius and height come from the global form styles (app/globals.css). */
export const CONTROL = "border px-2.5 text-sm";
/** The label above a filter control. */
export const FILTER_LABEL = "flex flex-col gap-1 text-xs font-medium text-secondary";

/** A search box with a magnifier: the same field wherever a list is searched. */
export function SearchField({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <span className="relative flex items-center">
      <svg aria-hidden="true" viewBox="0 0 24 24" className="pointer-events-none absolute left-2.5 size-4 text-muted" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
        <path d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4" />
      </svg>
      <input {...props} className={`${CONTROL} w-64 max-w-full pl-8 ${className}`} />
    </span>
  );
}
