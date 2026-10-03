"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";

/**
 * A button for something that cannot be undone. The first click only asks; the action runs on
 * the explicit second step. (Inline, not window.confirm: it can be tested and styled, and it
 * works with the keyboard.)
 */
export function ConfirmButton({
  label,
  confirmLabel,
  question,
  onConfirm,
  disabled = false,
  testId,
}: {
  label: string;
  confirmLabel: string;
  question: string;
  onConfirm: () => void;
  disabled?: boolean;
  testId?: string;
}) {
  const [asking, setAsking] = useState(false);

  if (!asking || disabled) {
    return (
      <Button type="button" disabled={disabled} onClick={() => setAsking(true)} data-testid={testId}>
        {label}
      </Button>
    );
  }
  return (
    <span role="group" aria-label={question} className="flex flex-wrap items-center gap-2 text-sm">
      <span>{question}</span>
      <Button
        type="button"
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
        data-testid={testId ? `${testId}-confirm` : undefined}
      >
        {confirmLabel}
      </Button>
      <Button type="button" onClick={() => setAsking(false)} data-testid={testId ? `${testId}-keep` : undefined}>
        Keep
      </Button>
    </span>
  );
}
