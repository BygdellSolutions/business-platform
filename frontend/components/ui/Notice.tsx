import type { ReactNode } from "react";

const TONES = {
  info: "border-zinc-300 bg-zinc-50 text-zinc-800 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200",
  error: "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100",
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
    <div role={tone === "error" ? "alert" : "status"} data-testid={testId} className={`rounded border px-3 py-2 text-sm ${TONES[tone]}`}>
      {children}
    </div>
  );
}
