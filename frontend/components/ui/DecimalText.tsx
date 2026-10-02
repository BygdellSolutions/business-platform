import type { DecimalString } from "@/lib/decimal";

/**
 * Renders a decimal exactly as the backend sent it ("850.00", "0.1", "9999999999.99").
 * No parsing, no rounding, no locale formatting: a string in, the same string out.
 */
export function DecimalText({ value, className }: { value: DecimalString | string; className?: string }) {
  return <span className={className ? `tabular-nums ${className}` : "tabular-nums"}>{value}</span>;
}
