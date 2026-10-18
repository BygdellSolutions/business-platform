import type { ButtonHTMLAttributes } from "react";

const BASE =
  "inline-flex h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50";

export const BUTTON_VARIANTS = {
  primary: `${BASE} bg-accent text-white shadow-xs hover:bg-accent-hover`,
  secondary: `${BASE} border border-border-strong bg-panel text-foreground hover:bg-surface-hover`,
  danger: `${BASE} bg-red-700 text-white shadow-xs hover:bg-red-800`,
  // Between primary and secondary: a soft accent tint and a thin accent edge, so a button among plain content (the invoice's action row)
  // is seen as something to press without competing with the page's one main action.
  tonal: `${BASE} border border-accent/35 bg-accent-soft text-foreground shadow-xs hover:border-accent/60 hover:bg-accent/20`,
} as const;

/** The same look for a link that starts the main action of a page ("New customer"). */
export const PRIMARY_LINK = BUTTON_VARIANTS.primary;

/** The same look for a link that leaves the page ("Back to orders", "Cancel"): a button, never bare underlined text. */
export const SECONDARY_LINK = BUTTON_VARIANTS.secondary;

/** A small drawn symbol for a button (decoration: the words carry the meaning). `path` is the stroke of a 24 × 24 outline icon. */
export function ButtonIcon({ path }: { path: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4 shrink-0 text-accent-icon" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

export const ICONS = {
  download: "M12 4v11M7 11l5 5 5-5M5 20h14",
  mail: "M3 6h18v12H3zM3 7l9 6 9-6",
  payment: "M3 7h18v10H3zM3 11h18M7 15h3",
  returns: "M9 7H5v4M5 7l3-3M5 11a7 7 0 1 1 2 5",
  credit: "M7 3h8l4 4v14H7zM15 3v5h4M10 13h6M10 17h4",
  invoice: "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6",
  stock: "M3 8l9-5 9 5v9l-9 5-9-5zM3 8l9 5 9-5M12 13v9",
  incoming: "M2 6h12v10H2zM14 10h4l4 3v3h-8M6 19a2 2 0 1 0 0-.01M18 19a2 2 0 1 0 0-.01",
  discount: "M3 12V4h8l10 10-8 8zM7.5 8.5h.01",
  journal: "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM9 8h6M9 12h6",
  document: "M7 3h8l4 4v14H7zM15 3v5h4M10 13h6M10 17h6",
  note: "M4 20h4L19 9l-4-4L4 16zM13 7l4 4",
  service: "M14 6a4 4 0 0 0 5 5l-9 9a2 2 0 0 1-3-3l9-9a4 4 0 0 1-2-2zM15 3l3 3",
  reopen: "M4 12a8 8 0 1 0 3-6.2M4 4v5h5",
} as const;

export function Button({
  className = "",
  variant,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof BUTTON_VARIANTS }) {
  // A form's submit button is its main action, so it stands out unless the caller says otherwise.
  const look = BUTTON_VARIANTS[variant ?? (props.type === "submit" ? "primary" : "secondary")];
  return <button {...props} className={`${look} ${className}`} />;
}
