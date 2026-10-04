"use client";

import { useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { newRequestKey } from "@/features/organizations/request-key";
import { apiFetchAccount } from "@/lib/api/client";
import type { Organization, OrganizationCreate } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["name", "default_currency"] as const;

/**
 * Create an organization and become its owner. Deliberately small: a name and a currency; the rest of the
 * business profile is completed later in Settings (and invoicing keeps its own checks).
 *
 * The currency is chosen by the person: the field starts empty and nothing is assumed (no SEK, nothing from a
 * country or locale). It is the same typed three-letter code as in Settings; the backend judges it.
 *
 * Retrying: each attempt carries a random request key. If the outcome is UNKNOWN (the network failed or the
 * server answered with an error after possibly committing) the same key and the same details are sent again, and
 * the backend returns the organization it already created instead of making another. A definite refusal (the
 * values were not valid, the account may not create organizations) discards the key.
 *
 * After success the page does a FULL navigation to `/o/{id}`: the organization is selected by the URL alone, and
 * nothing about it is stored (no cookie, no storage, no state that another tab could share).
 */
export function CreateOrganizationForm() {
  const { pending, error, run } = useMutation();
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState("");
  const [local, setLocal] = useState<Record<string, string[]>>({});
  const [unknownOutcome, setUnknownOutcome] = useState(false);
  const attempt = useRef<{ key: string; fingerprint: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: OrganizationCreate = { name: name.trim(), default_currency: currency.trim() };
    const missing: Record<string, string[]> = {};
    if (body.name === "") missing.name = ["Enter a name for the organization."];
    if (body.default_currency === "") missing.default_currency = ["Choose the currency the organization works in."];
    setLocal(missing);
    if (Object.keys(missing).length > 0) return;

    // The same details after an unknown outcome reuse the key; changed details are a different request.
    const fingerprint = JSON.stringify(body);
    if (attempt.current === null || attempt.current.fingerprint !== fingerprint) attempt.current = { key: newRequestKey(), fingerprint };
    const key = attempt.current.key;

    let outcomeUnknown = false;
    const created = await run(async () => {
      const result = await apiFetchAccount<Organization>({ method: "POST", body, idempotencyKey: key });
      if (!result.ok && (result.error.kind === "network" || result.error.kind === "server")) outcomeUnknown = true;
      return result;
    });
    setUnknownOutcome(outcomeUnknown);
    if (created === null) {
      if (!outcomeUnknown) attempt.current = null;
      return;
    }
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`/o/${encodeURIComponent(created.id)}`);
  }

  const problems = problemsFrom(error, CONTROLS);
  const refused = error?.kind === "forbidden";

  return (
    <form onSubmit={(event) => void submit(event)} noValidate className="flex max-w-xl flex-col gap-4" data-testid="create-organization-form">
      <TextField label="Organization name" name="name" value={name} onChange={setName} required error={local.name ?? problems.byField.name} autoComplete="organization" hint="Shown in the app. Other organizations may use the same name." />
      <TextField
        label="Currency"
        name="default_currency"
        value={currency}
        onChange={setCurrency}
        required
        error={local.default_currency ?? problems.byField.default_currency}
        autoComplete="off"
        hint="A three-letter code, for example EUR. It can be changed only until the first item or transaction exists."
      />
      {refused ? <Notice tone="error" testId="creation-refused">This account is not allowed to create organizations.</Notice> : <ErrorSummary messages={problems.general} />}
      {unknownOutcome && (
        <Notice tone="error" testId="creation-unknown">
          We could not confirm whether the organization was created. Try again with the same details: it will not be created twice. You can also check{" "}
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
          <a href="/" className="underline">your organizations</a>.
        </Notice>
      )}
      <div>
        <Button type="submit" disabled={pending} data-testid="submit">
          {pending ? "Creating…" : "Create organization"}
        </Button>
      </div>
    </form>
  );
}
