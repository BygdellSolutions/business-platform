"use client";

import { useState } from "react";

/** A value people need to read out or paste elsewhere (an id): shown in full, selectable, with a Copy button. */
export function CopyableValue({ label, value, testId, hint }: { label: string; value: string; testId: string; hint?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false); // the value is selectable; copying by hand works
    }
  }

  return (
    <div className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      <div className="flex flex-wrap items-center gap-2">
        <code className="rounded bg-zinc-100 px-2 py-1 font-mono select-all dark:bg-zinc-800" data-testid={testId}>
          {value}
        </code>
        <button type="button" onClick={() => void copy()} className="rounded border border-zinc-400 px-2 py-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      {hint && <span className="text-xs text-zinc-500">{hint}</span>}
    </div>
  );
}
