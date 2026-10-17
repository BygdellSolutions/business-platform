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
  alsoLabel,
  onAlso,
  declineLabel = "No, go back",
  oneLine = false,
  question,
  onConfirm,
  disabled = false,
  testId,
}: {
  label: string;
  confirmLabel: string;
  /** A second way to go ahead, shown between the two ("Yes, cancel it and the draft order"). Both are needed for it to appear. */
  alsoLabel?: string;
  onAlso?: () => void;
  /** The button that backs out. It says what happens ("No, go back", "No, back to the draft"): a bare "Keep" does not say what is kept. */
  declineLabel?: string;
  /** The question and its buttons stay on one line (in a table row, where a wrapped question would make the row taller). */
  oneLine?: boolean;
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
    <span role="group" aria-label={question} className={`flex items-center gap-2 text-sm ${oneLine ? "flex-nowrap whitespace-nowrap" : "flex-wrap"}`}>
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
      {alsoLabel !== undefined && onAlso !== undefined && (
        <Button
          type="button"
          onClick={() => {
            setAsking(false);
            onAlso();
          }}
          data-testid={testId ? `${testId}-also` : undefined}
        >
          {alsoLabel}
        </Button>
      )}
      <Button type="button" onClick={() => setAsking(false)} data-testid={testId ? `${testId}-keep` : undefined}>
        {declineLabel}
      </Button>
    </span>
  );
}
