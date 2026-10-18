"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { CopyableValue } from "@/components/ui/CopyableValue";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Notice } from "@/components/ui/Notice";
import { TextAreaField, TextField } from "@/components/ui/Field";
import { apiFetchSupport } from "@/lib/api/client";
import { problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["name", "email", "message"] as const;

export interface SupportReceipt {
  reference: string;
}

/**
 * The support message box. Someone who is signed in has their name and address from the account (the server decides that too, so
 * nothing typed here can pose as another account); everyone else says who they are so we can answer. The field called `website`
 * is a trap: it is hidden from people and only a program fills it.
 */
export function SupportForm({ signedInAs }: { signedInAs: { name: string; email: string } | null }) {
  const { pending, error, run } = useMutation();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [trap, setTrap] = useState("");
  const [receipt, setReceipt] = useState<SupportReceipt | null>(null);
  const problems = problemsFrom(error, CONTROLS);
  // A structured refusal ({code, message}) carries its own wording (too many messages, busy).
  const general = error !== null && error.kind !== "validation" ? [error.message] : problems.general;

  async function submit(event: FormEvent) {
    event.preventDefault();
    // For someone signed in the server uses the account's own name and address whatever is sent; they are included so the request is complete.
    const body = { name: signedInAs?.name ?? name, email: signedInAs?.email ?? email, message, website: trap };
    const sent = await run(() => apiFetchSupport<SupportReceipt>({ method: "POST", body }));
    if (sent === null) return;
    setReceipt(sent);
    setMessage("");
  }

  if (receipt !== null) {
    return (
      <div className="flex flex-col gap-3" data-testid="support-sent">
        <Notice testId="support-thanks">Thank you. Your message has been sent to support.</Notice>
        <CopyableValue label="Your ticket number" value={receipt.reference} testId="support-reference" hint="Keep it, and mention it if you write to us again about this." />
        <p className="text-sm text-secondary">
          We answer by email to {signedInAs?.email ?? (email || "the address you gave")}. A confirmation with the ticket number is on its way there too.
        </p>
        <div>
          <Button type="button" onClick={() => setReceipt(null)} data-testid="support-another">
            Write another message
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Message to support" className="flex flex-col gap-3" data-testid="support-form">
      {signedInAs === null ? (
        <>
          <TextField label="Your name (optional)" name="name" value={name} onChange={setName} autoComplete="name" error={problems.byField.name} />
          <TextField label="Your email" name="email" value={email} onChange={setEmail} inputMode="email" autoComplete="email" error={problems.byField.email} />
        </>
      ) : (
        <p className="text-sm text-secondary" data-testid="support-signed-in">
          We will answer {signedInAs.name || signedInAs.email} at {signedInAs.email}.
        </p>
      )}
      <TextAreaField label="How can we help?" name="message" value={message} onChange={setMessage} rows={7} error={problems.byField.message} />
      <div className="text-xs text-muted" data-testid="support-count">
        {message.trim().length} / 4000
      </div>
      {/* The trap: off screen and out of the tab order, so no person fills it. */}
      <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
        <label>
          Website
          <input type="text" name="website" tabIndex={-1} autoComplete="off" value={trap} onChange={(event) => setTrap(event.target.value)} data-testid="support-trap" />
        </label>
      </div>
      <ErrorSummary messages={general} />
      <div>
        <Button type="submit" disabled={pending || message.trim() === ""} data-testid="support-send">
          {pending ? "Sending…" : "Send message"}
        </Button>
      </div>
    </form>
  );
}
