import type { ButtonHTMLAttributes } from "react";

const BASE =
  "inline-flex h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50";

export const BUTTON_VARIANTS = {
  primary: `${BASE} bg-accent text-white shadow-xs hover:bg-accent-hover`,
  secondary: `${BASE} border border-border-strong bg-panel text-foreground hover:bg-surface-hover`,
  danger: `${BASE} bg-red-700 text-white shadow-xs hover:bg-red-800`,
} as const;

/** The same look for a link that starts the main action of a page ("New customer"). */
export const PRIMARY_LINK = BUTTON_VARIANTS.primary;

/** The same look for a link that leaves the page ("Back to orders", "Cancel"): a button, never bare underlined text. */
export const SECONDARY_LINK = BUTTON_VARIANTS.secondary;

export function Button({
  className = "",
  variant,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof BUTTON_VARIANTS }) {
  // A form's submit button is its main action, so it stands out unless the caller says otherwise.
  const look = BUTTON_VARIANTS[variant ?? (props.type === "submit" ? "primary" : "secondary")];
  return <button {...props} className={`${look} ${className}`} />;
}
