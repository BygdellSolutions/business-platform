import type { ButtonHTMLAttributes } from "react";

const BASE = "rounded-md px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50";

export const BUTTON_VARIANTS = {
  primary: `${BASE} bg-accent font-medium text-white shadow-sm hover:bg-accent-hover`,
  secondary: `${BASE} border border-zinc-300 bg-white hover:bg-zinc-50 dark:border-zinc-600 dark:bg-zinc-900 dark:hover:bg-zinc-800`,
  danger: `${BASE} bg-red-700 font-medium text-white shadow-sm hover:bg-red-800`,
} as const;

/** The same look for a link that starts the main action of a page ("New customer"). */
export const PRIMARY_LINK = `inline-block ${BUTTON_VARIANTS.primary}`;

export function Button({
  className = "",
  variant,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof BUTTON_VARIANTS }) {
  // A form's submit button is its main action, so it stands out unless the caller says otherwise.
  const look = BUTTON_VARIANTS[variant ?? (props.type === "submit" ? "primary" : "secondary")];
  return <button {...props} className={`${look} ${className}`} />;
}
