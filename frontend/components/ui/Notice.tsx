import type { ReactNode } from "react";

const TONES = {
  info: "border border-border bg-surface-hover text-foreground",
  warning: "tint tone-warning text-foreground",
  error: "tint tone-danger text-foreground",
} as const;

export function Notice({
  tone = "info",
  children,
  testId,
}: {
  tone?: keyof typeof TONES;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div role={tone === "error" ? "alert" : "status"} data-testid={testId} className={`rounded-lg px-3 py-2 text-sm ${TONES[tone]}`}>
      {children}
    </div>
  );
}
